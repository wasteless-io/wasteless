"""
Unit tests for src/reports/daily_briefing.py — the parts the existing
ui/tests/test_briefing.py does NOT cover: the deterministic SQL
aggregation (collect_briefing_data) and generate_briefing's content
paths. Prompt building, silent degradation and the one-row-per-day
cache are covered UI-side; they are not duplicated here.

litellm and the database are always mocked.
"""

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.llm import MODEL_ENV_VAR
from reports.daily_briefing import collect_briefing_data, generate_briefing


def _mock_conn(
    total=(250.0, 4),
    by_type=(("ec2_instance", 2, 200.0), ("ebs_volume", 2, 50.0)),
    trend=(240.0, 300.0),
    pending=(3, 180.0, 12),
    top_pending=(("STOP instance i-1", "ec2_instance", 150.0, 12),),
    actions=(2, 1, 5),
    savings=(45.2,),
    month_spend=(1000.0,),
    last_scan=(2.5,),
):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    # fetchone/fetchall consumption order mirrors collect_briefing_data
    cursor.fetchone.side_effect = [total, trend, pending, actions, savings, month_spend, last_scan]
    cursor.fetchall.side_effect = [list(by_type), list(top_pending)]
    return conn


class TestCollectBriefingData:

    def test_aggregates_all_sections(self):
        data = collect_briefing_data(_mock_conn())

        assert data["active_waste"]["monthly_eur"] == 250.0
        assert data["active_waste"]["count"] == 4
        assert data["active_waste"]["by_type"][0] == ("ec2_instance", 2, 200.0)
        assert data["waste_trend"] == {"yesterday_eur": 240.0, "week_ago_eur": 300.0}
        assert data["pending"]["count"] == 3
        assert data["pending"]["top"][0]["monthly_eur"] == 150.0
        assert data["actions_7d"] == {"succeeded": 2, "failed": 1, "dry_run": 5}
        assert data["verified_savings_eur"] == 45.2
        assert data["last_scan_hours_ago"] == 2.5

    def test_waste_rate_from_last_month_spend(self):
        data = collect_briefing_data(_mock_conn(total=(250.0, 4), month_spend=(1000.0,)))
        assert data["waste_rate_pct"] == 25.0

    def test_no_cost_data_means_no_waste_rate(self):
        """Without Cost Explorer data the denominator is 0 — the rate must
        be None (omitted from the briefing), not a division error or 0%."""
        data = collect_briefing_data(_mock_conn(month_spend=(0,)))
        assert data["waste_rate_pct"] is None
        assert data["last_month_spend_eur"] is None

    def test_missing_snapshots_mean_null_trend(self):
        data = collect_briefing_data(_mock_conn(trend=(None, None)))
        assert data["waste_trend"] == {"yesterday_eur": None, "week_ago_eur": None}

    def test_no_pending_recommendations(self):
        data = collect_briefing_data(_mock_conn(pending=(0, 0, None), top_pending=()))
        assert data["pending"]["count"] == 0
        assert data["pending"]["oldest_days"] is None
        assert data["pending"]["top"] == []

    def test_empty_waste_table_means_null_scan_age(self):
        data = collect_briefing_data(_mock_conn(last_scan=(None,)))
        assert data["last_scan_hours_ago"] is None


class TestGenerateBriefing:

    def _mock_litellm(self, content="Waste is stable at 250 €."):
        mock = MagicMock()
        mock.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))]
        )
        return mock

    def test_returns_stripped_content(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, "gpt-4o-mini")
        mock = self._mock_litellm("  Waste is stable.  ")
        with patch.dict(sys.modules, {"litellm": mock}):
            assert generate_briefing({}) == "Waste is stable."

    def test_provider_error_returns_none(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, "gpt-4o-mini")
        mock = MagicMock()
        mock.completion.side_effect = RuntimeError("rate limited")
        with patch.dict(sys.modules, {"litellm": mock}):
            assert generate_briefing({}) is None

    def test_empty_completion_returns_none(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, "gpt-4o-mini")
        mock = self._mock_litellm(content=None)
        with patch.dict(sys.modules, {"litellm": mock}):
            assert generate_briefing({}) is None
