"""
Unit tests for src/reports/weekly_digest.py — activity report over a date
range, with optional LLM narrative. litellm and the database are always
mocked.
"""

import sys
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.llm import MODEL_ENV_VAR
from reports import weekly_digest
from reports.weekly_digest import (
    build_digest,
    collect_digest_data,
    format_digest,
    generate_narrative,
)

TODAY = date.today()
WEEK_AGO = TODAY - timedelta(days=6)


def _mock_conn(new_waste=(3, 120.5),
               by_type=(('ec2_instance', 2, 98.0), ('ebs_volume', 1, 22.5)),
               pending=(8, 312.0, 12),
               actions=(3, 1, 2),
               verified=(45.2,)):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = [new_waste, pending, actions, verified]
    cursor.fetchall.return_value = list(by_type)
    return conn


SAMPLE_DATA = {
    'period': {'start': '2026-06-28', 'end': '2026-07-04', 'days': 7},
    'new_waste': {'count': 3, 'monthly_eur': 120.5,
                  'by_type': [('ec2_instance', 2, 98.0), ('ebs_volume', 1, 22.5)]},
    'pending': {'count': 8, 'monthly_eur': 312.0, 'oldest_days': 12,
                'scope': 'snapshot'},
    'actions': {'succeeded': 3, 'failed': 1, 'dry_run': 2},
    'verified_savings_eur': 45.2,
}


class TestCollectDigestData:

    def test_aggregates_all_sections(self):
        data = collect_digest_data(_mock_conn(), WEEK_AGO, TODAY)
        assert data['period'] == {'start': WEEK_AGO.isoformat(),
                                  'end': TODAY.isoformat(), 'days': 7}
        assert data['new_waste']['count'] == 3
        assert data['new_waste']['monthly_eur'] == 120.5
        assert data['new_waste']['by_type'][0] == ('ec2_instance', 2, 98.0)
        assert data['pending'] == {'count': 8, 'monthly_eur': 312.0,
                                   'oldest_days': 12, 'scope': 'snapshot'}
        assert data['actions'] == {'succeeded': 3, 'failed': 1, 'dry_run': 2}
        assert data['verified_savings_eur'] == 45.2

    def test_dict_rows_supported(self):
        """The UI connects with RealDictCursor: rows are dicts, not tuples."""
        conn = _mock_conn(
            new_waste={'count': 3, 'coalesce': 120.5},
            by_type=({'resource_type': 'ec2_instance', 'count': 2, 'coalesce': 98.0},),
            pending={'count': 8, 'coalesce': 312.0, 'extract': 12},
            actions={'a': 3, 'b': 1, 'c': 2},
            verified={'coalesce': 45.2})
        data = collect_digest_data(conn, WEEK_AGO, TODAY)
        assert data['new_waste']['monthly_eur'] == 120.5
        assert data['new_waste']['by_type'] == [('ec2_instance', 2, 98.0)]
        assert data['pending']['oldest_days'] == 12
        assert data['verified_savings_eur'] == 45.2

    def test_empty_database(self):
        conn = _mock_conn(new_waste=(0, 0), by_type=(),
                          pending=(0, 0, None), actions=(0, 0, 0),
                          verified=(0,))
        data = collect_digest_data(conn, WEEK_AGO, TODAY)
        assert data['new_waste']['count'] == 0
        assert data['pending']['oldest_days'] is None
        assert data['verified_savings_eur'] == 0.0

    def test_period_including_today_uses_pending_snapshot(self):
        conn = _mock_conn()
        data = collect_digest_data(conn, WEEK_AGO, TODAY)
        assert data['pending']['scope'] == 'snapshot'
        pending_sql = conn.cursor.return_value.execute.call_args_list[2][0][0]
        assert "status = 'pending'" in pending_sql

    def test_past_period_counts_recommendations_created(self):
        conn = _mock_conn(pending=(5, 99.0))
        start, end = TODAY - timedelta(days=40), TODAY - timedelta(days=10)
        data = collect_digest_data(conn, start, end)
        assert data['pending'] == {'count': 5, 'monthly_eur': 99.0,
                                   'oldest_days': None,
                                   'scope': 'created_in_period'}
        pending_sql = conn.cursor.return_value.execute.call_args_list[2][0][0]
        assert 'created_at' in pending_sql
        assert "status = 'pending'" not in pending_sql

    def test_bounds_are_inclusive_dates(self):
        conn = _mock_conn(pending=(5, 99.0))  # past period: 2-column pending
        start, end = date(2026, 6, 1), date(2026, 6, 30)
        collect_digest_data(conn, start, end)
        params = conn.cursor.return_value.execute.call_args_list[0][0][1]
        assert params == (start, date(2026, 7, 1)), \
            "end bound must be exclusive at end_date + 1 day"

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError):
            collect_digest_data(_mock_conn(), TODAY, TODAY - timedelta(days=1))

    def test_cursor_closed(self):
        conn = _mock_conn()
        collect_digest_data(conn, WEEK_AGO, TODAY)
        conn.cursor.return_value.close.assert_called_once()


class TestFormatDigest:

    def test_contains_all_numbers(self):
        text = format_digest(SAMPLE_DATA)
        assert '2026-06-28 to 2026-07-04' in text
        assert '3 resource(s), 120.50 EUR/month' in text
        assert 'ec2_instance: 2 (98.00 EUR/month)' in text
        assert '8 (312.00 EUR/month of potential savings)' in text
        assert 'waiting 12 day(s)' in text
        assert '3 succeeded, 1 failed (2 dry-run)' in text
        assert '45.20 EUR' in text

    def test_no_oldest_line_when_no_pending(self):
        data = dict(SAMPLE_DATA,
                    pending={'count': 0, 'monthly_eur': 0.0,
                             'oldest_days': None, 'scope': 'snapshot'})
        assert 'waiting' not in format_digest(data)

    def test_past_period_wording(self):
        data = dict(SAMPLE_DATA,
                    pending={'count': 5, 'monthly_eur': 99.0,
                             'oldest_days': None,
                             'scope': 'created_in_period'})
        text = format_digest(data)
        assert 'created in the period: 5' in text
        assert 'Pending recommendations' not in text


class TestGenerateNarrative:

    def _mock_litellm(self, content='A quiet week.'):
        mock = MagicMock()
        mock.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))])
        return mock

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        assert generate_narrative(SAMPLE_DATA) is None

    def test_returns_stripped_content(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': self._mock_litellm('  text  ')}):
            assert generate_narrative(SAMPLE_DATA) == 'text'

    def test_prompt_contains_data_and_guardrail(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        mock = self._mock_litellm()
        with patch.dict(sys.modules, {'litellm': mock}):
            generate_narrative(SAMPLE_DATA)
        prompt = mock.completion.call_args[1]['messages'][0]['content']
        assert '312.0' in prompt
        assert 'Never invent numbers' in prompt

    def test_provider_error_returns_none(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        mock = MagicMock()
        mock.completion.side_effect = RuntimeError('rate limited')
        with patch.dict(sys.modules, {'litellm': mock}):
            assert generate_narrative(SAMPLE_DATA) is None


class TestBuildDigest:

    def test_without_llm_is_deterministic_only(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        digest = build_digest(_mock_conn(), WEEK_AGO, TODAY)
        assert digest.startswith('Wasteless — activity report')
        assert '312.00 EUR/month' in digest

    def test_with_llm_prepends_narrative(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': MagicMock()}), \
             patch.object(weekly_digest, 'generate_narrative',
                          return_value='Busy week: 120 EUR of new waste.'):
            digest = build_digest(_mock_conn(), WEEK_AGO, TODAY)
        assert digest.startswith('Busy week')
        assert 'Wasteless — activity report' in digest

    def test_failed_narrative_falls_back_silently(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': MagicMock()}), \
             patch.object(weekly_digest, 'generate_narrative', return_value=None):
            digest = build_digest(_mock_conn(), WEEK_AGO, TODAY)
        assert digest.startswith('Wasteless — activity report')
