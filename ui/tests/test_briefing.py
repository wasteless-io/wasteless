#!/usr/bin/env python3
"""
Unit tests for the daily CTO briefing (src/reports/daily_briefing.py via
the ui/utils/reports.py bridge) — prompt building, silent degradation,
and the one-row-per-day cache logic. No database or LLM required.
"""

import unittest
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path (and, through utils.reports, backend src/)
sys.path.insert(0, str(Path(__file__).parent.parent))

import utils.reports  # noqa: F401  — wires backend src/ into sys.path
from reports import daily_briefing


SAMPLE_DATA = {
    'date': '2026-07-05',
    'active_waste': {'monthly_eur': 10.53, 'count': 11,
                     'by_type': [('ebs_snapshot', 9, 9.57)]},
    'waste_trend': {'yesterday_eur': 10.53, 'week_ago_eur': 8.2},
    'pending': {'count': 2, 'monthly_eur': 3.4, 'oldest_days': 12, 'top': []},
    'actions_7d': {'succeeded': 1, 'failed': 0, 'dry_run': 5},
    'verified_savings_eur': 0.0,
    'last_month_spend_eur': None,
    'waste_rate_pct': None,
    'last_scan_hours_ago': 2.0,
}


class FakeCursor:
    """Minimal cursor: returns queued rows, records executed SQL."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append(query)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self._cursor = FakeCursor(rows)
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class TestBriefingPrompt(unittest.TestCase):

    def test_prompt_contains_the_numbers(self):
        prompt = daily_briefing.build_briefing_prompt(SAMPLE_DATA)
        self.assertIn('10.53', prompt)
        self.assertIn('ebs_snapshot', prompt)
        self.assertIn('Never invent numbers', prompt)

    def test_prompt_asks_for_plain_text(self):
        prompt = daily_briefing.build_briefing_prompt(SAMPLE_DATA)
        self.assertIn('no markdown', prompt)


class TestSilentDegradation(unittest.TestCase):

    def test_generate_returns_none_when_llm_disabled(self):
        with patch.object(daily_briefing.llm, 'is_enabled', return_value=False):
            self.assertIsNone(daily_briefing.generate_briefing(SAMPLE_DATA))

    def test_get_or_create_returns_none_when_disabled_and_no_cache(self):
        conn = FakeConn(rows=[None])  # cache miss
        with patch.object(daily_briefing.llm, 'is_enabled', return_value=False):
            self.assertIsNone(daily_briefing.get_or_create_briefing(conn))

    def test_get_or_create_never_raises_on_db_error(self):
        class BrokenConn:
            def cursor(self):
                raise RuntimeError('db down')

            def rollback(self):
                pass

        self.assertIsNone(daily_briefing.get_or_create_briefing(BrokenConn()))


class TestBriefingCache(unittest.TestCase):

    def test_cache_hit_returns_without_calling_llm(self):
        created = datetime(2026, 7, 5, 8, 0)
        conn = FakeConn(rows=[('cached text', 'anthropic/claude', created)])
        with patch.object(daily_briefing, 'generate_briefing') as gen:
            result = daily_briefing.get_or_create_briefing(conn)
        gen.assert_not_called()
        self.assertEqual(result['content'], 'cached text')
        self.assertTrue(result['cached'])

    def test_cache_hit_with_dict_rows(self):
        created = datetime(2026, 7, 5, 8, 0)
        conn = FakeConn(rows=[{'content': 'cached text',
                               'model': 'm', 'created_at': created}])
        result = daily_briefing.get_or_create_briefing(conn)
        self.assertEqual(result['content'], 'cached text')

    def test_refresh_skips_cache_and_regenerates(self):
        created = datetime(2026, 7, 5, 9, 30)
        conn = FakeConn(rows=[(created,)])  # only the INSERT..RETURNING row
        with patch.object(daily_briefing.llm, 'is_enabled', return_value=True), \
             patch.object(daily_briefing, 'collect_briefing_data',
                          return_value=SAMPLE_DATA), \
             patch.object(daily_briefing, 'generate_briefing',
                          return_value='fresh text') as gen:
            result = daily_briefing.get_or_create_briefing(conn, refresh=True)
        gen.assert_called_once()
        self.assertEqual(result['content'], 'fresh text')
        self.assertFalse(result['cached'])
        self.assertTrue(conn.committed)
        # No SELECT against the cache: first statement is the upsert
        self.assertIn('INSERT INTO daily_briefings', conn._cursor.executed[0])

    def test_generation_failure_returns_none_without_insert(self):
        conn = FakeConn(rows=[None])  # cache miss
        with patch.object(daily_briefing.llm, 'is_enabled', return_value=True), \
             patch.object(daily_briefing, 'collect_briefing_data',
                          return_value=SAMPLE_DATA), \
             patch.object(daily_briefing, 'generate_briefing', return_value=None):
            self.assertIsNone(daily_briefing.get_or_create_briefing(conn))
        self.assertFalse(conn.committed)


if __name__ == '__main__':
    unittest.main()
