#!/usr/bin/env python3
"""Cost statement aggregation (utils/cost_report.py): period resolution for
day/week/month/year, anchor navigation, and the SQL aggregation shaped from a
mocked cloud_costs_raw. No real DB."""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.cost_report import (
    resolve_cost_period,
    shift_anchor,
    collect_cost_report,
    format_cost_statement,
    _trend_scale,
)


class TestTrendScale(unittest.TestCase):
    """The chart scale ignores a single lumpy day so normal days stay readable;
    only genuine spikes (>4x median) clip."""

    def test_single_spike_does_not_set_scale(self):
        # 29 normal days ~0.8, one tax spike at 4.64.
        trend = [(f"d{i}", 0.8) for i in range(29)] + [("d29", 4.64)]
        scale = _trend_scale(trend)
        self.assertAlmostEqual(scale, 0.8, places=2)  # capped at the normal max
        self.assertLess(scale, 4.64)
        self.assertTrue(any(v > scale for _, v in trend))  # the spike clips

    def test_normal_variation_not_clipped(self):
        trend = [("a", 0.4), ("b", 0.8), ("c", 1.0), ("d", 0.6)]
        scale = _trend_scale(trend)
        self.assertEqual(scale, 1.0)  # highest normal day, nothing clipped
        self.assertFalse(any(v > scale for _, v in trend))

    def test_empty_and_zero(self):
        self.assertEqual(_trend_scale([]), 0.0)
        self.assertEqual(_trend_scale([("a", 0), ("b", 0)]), 0.0)


class TestResolvePeriod(unittest.TestCase):
    def test_month(self):
        s, e, label, ps, pe, bucket = resolve_cost_period("month", date(2026, 6, 15))
        self.assertEqual((s, e), (date(2026, 6, 1), date(2026, 6, 30)))
        self.assertEqual((ps, pe), (date(2026, 5, 1), date(2026, 5, 31)))
        self.assertEqual(label, "June 2026")
        self.assertEqual(bucket, "day")

    def test_year(self):
        s, e, label, ps, pe, bucket = resolve_cost_period("year", date(2026, 7, 18))
        self.assertEqual((s, e), (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual((ps, pe), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(label, "2026")
        self.assertEqual(bucket, "month")

    def test_week_starts_monday(self):
        # 2026-07-15 is a Wednesday.
        s, e, _, ps, pe, bucket = resolve_cost_period("week", date(2026, 7, 15))
        self.assertEqual(s.weekday(), 0)
        self.assertEqual((e - s).days, 6)
        self.assertEqual((s - ps).days, 7)
        self.assertEqual(bucket, "day")

    def test_day(self):
        s, e, label, ps, pe, bucket = resolve_cost_period("day", date(2026, 7, 15))
        self.assertEqual(s, e)
        self.assertEqual(ps, date(2026, 7, 14))
        self.assertEqual(bucket, "day")

    def test_unknown_granularity_falls_back_to_month(self):
        _, _, label, _, _, _ = resolve_cost_period("decade", date(2026, 6, 15))
        self.assertEqual(label, "June 2026")


class TestShiftAnchor(unittest.TestCase):
    def test_month_prev_next(self):
        self.assertEqual(shift_anchor("month", date(2026, 3, 10), -1), date(2026, 2, 1))
        self.assertEqual(shift_anchor("month", date(2026, 12, 10), +1), date(2027, 1, 1))

    def test_year(self):
        self.assertEqual(shift_anchor("year", date(2026, 6, 1), -1).year, 2025)

    def test_week(self):
        self.assertEqual((date(2026, 7, 15) - shift_anchor("week", date(2026, 7, 15), -1)).days, 7)


class TestCollectCostReport(unittest.TestCase):
    def _conn(self, latest, total, prev_total, cur_svc, prev_svc, trend):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.side_effect = [(latest,), (total,), (prev_total,)]
        cur.fetchall.side_effect = [cur_svc, prev_svc, trend]
        return conn

    def test_aggregates_and_ranks_services(self):
        conn = self._conn(
            latest=date(2026, 6, 30),
            total=19.96,
            prev_total=20.76,
            cur_svc=[("AWS WAF", 8.0), ("Tax", 3.33), ("EC2 - Other", 8.63)],
            prev_svc=[("AWS WAF", 8.0), ("Tax", 3.5)],
            trend=[(date(2026, 6, 1), 10.0), (date(2026, 6, 2), 9.96)],
        )
        r = collect_cost_report(conn, "month", date(2026, 6, 15))
        self.assertAlmostEqual(r["total_usd"], 19.96)
        self.assertAlmostEqual(r["prev_total_usd"], 20.76)
        self.assertEqual(r["delta_pct"], round((19.96 - 20.76) / 20.76 * 100, 1))
        # Sorted by cost desc: EC2 - Other (8.63) first, then AWS WAF (8.0).
        self.assertEqual(r["services"][0]["service"], "EC2 - Other")
        self.assertEqual(r["top_service"]["service"], "EC2 - Other")
        # AWS WAF unchanged vs prev -> 0% delta; Tax down.
        waf = next(s for s in r["services"] if s["service"] == "AWS WAF")
        self.assertEqual(waf["delta_pct"], 0.0)
        # EC2 - Other has no prev -> delta None (rendered as "new").
        ec2 = next(s for s in r["services"] if s["service"] == "EC2 - Other")
        self.assertIsNone(ec2["delta_pct"])
        self.assertEqual(r["service_count"], 3)
        self.assertTrue(r["complete"])  # end 06-30 <= latest 06-30
        self.assertEqual(len(r["trend"]), 2)

    def test_partial_period_not_complete(self):
        conn = self._conn(
            latest=date(2026, 7, 10),  # before end of July
            total=5.0,
            prev_total=20.0,
            cur_svc=[("EC2 - Other", 5.0)],
            prev_svc=[("EC2 - Other", 20.0)],
            trend=[(date(2026, 7, 1), 5.0)],
        )
        r = collect_cost_report(conn, "month", date(2026, 7, 15))
        self.assertFalse(r["complete"])
        # daily average uses elapsed days (to latest), not the full month.
        self.assertGreater(r["daily_avg_usd"], 0)

    def test_no_prev_data_delta_none(self):
        conn = self._conn(
            latest=date(2026, 12, 31),
            total=100.0,
            prev_total=0.0,
            cur_svc=[("EC2 - Other", 100.0)],
            prev_svc=[],
            trend=[(date(2026, 1, 1), 100.0)],
        )
        r = collect_cost_report(conn, "year", date(2026, 6, 1))
        self.assertIsNone(r["delta_pct"])


class TestCostAnalyst(unittest.TestCase):
    """The dashboard Cost Analyst read: 6-month trend, MoM, driver, spike."""

    def _conn(self, latest, months, svcs, peak_drivers, last30):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.side_effect = [(latest,), (last30,)]
        cur.fetchall.side_effect = [months, svcs, peak_drivers]
        return conn

    def test_trend_driver_and_spike(self):
        from utils.cost_report import cost_analyst

        months = [
            (date(2026, 2, 1), 59.8),
            (date(2026, 3, 1), 25.13),
            (date(2026, 4, 1), 19.73),
            (date(2026, 5, 1), 20.76),
            (date(2026, 6, 1), 19.96),
            (date(2026, 7, 1), 11.82),
        ]
        conn = self._conn(
            latest=date(2026, 7, 18),  # partial July
            months=months,
            svcs=[("EC2 - Other", 44.06), ("AWS WAF", 20.0)],
            peak_drivers=[("EC2 - Other", 22.0), ("Tax", 10.0)],
            last30=16.5,
        )
        d = cost_analyst(conn)
        self.assertEqual(d["current"]["label"], "Jul 2026")
        self.assertTrue(d["current_partial"])  # 18th < end of July
        self.assertIsNone(d["mom_delta_pct"])  # dropped because partial
        self.assertEqual(d["top_service"]["service"], "EC2 - Other")
        self.assertEqual(d["peak"]["label"], "Feb 2026")
        self.assertTrue(d["peak"]["is_spike"])  # 59.8 >= 2x median of others
        self.assertEqual([x["service"] for x in d["peak"]["drivers"]], ["EC2 - Other", "Tax"])
        self.assertAlmostEqual(d["annual_run_rate_usd"], 16.5 * 365 / 30)

    def test_full_month_gives_mom(self):
        from utils.cost_report import cost_analyst

        months = [(date(2026, 5, 1), 20.0), (date(2026, 6, 1), 25.0)]
        conn = self._conn(
            latest=date(2026, 6, 30),  # full June
            months=months,
            svcs=[("EC2 - Other", 45.0)],
            peak_drivers=[("EC2 - Other", 25.0)],
            last30=25.0,
        )
        d = cost_analyst(conn)
        self.assertFalse(d["current_partial"])
        self.assertEqual(d["mom_delta_pct"], 25.0)  # (25-20)/20


class TestFormatCostAnalyst(unittest.TestCase):
    def test_partial_month_says_so_far(self):
        from utils.cost_report import format_cost_analyst

        d = {
            "has_data": True,
            "current": {"label": "Jul 2026", "usd": 11.82},
            "current_partial": True,
            "previous": {"label": "Jun 2026", "usd": 19.96},
            "mom_delta_pct": None,
            "window_months": 6,
            "top_service": {"service": "EC2 - Other", "usd": 44.06, "pct": 28},
            "peak": {
                "label": "Feb 2026",
                "usd": 59.8,
                "ratio": 3.0,
                "is_spike": True,
                "drivers": [{"service": "EC2 - Other", "usd": 22}, {"service": "Tax", "usd": 10}],
            },
            "annual_run_rate_usd": 201.0,
        }
        text = format_cost_analyst(d)
        self.assertIn("so far is $11.82 (partial month)", text)
        self.assertNotIn("vs Jun 2026", text)  # no misleading MoM on a partial month
        self.assertIn("Feb 2026 stands out", text)
        self.assertIn("EC2 - Other ($44.06, 28%)", text)


class TestFormatCostContext(unittest.TestCase):
    """The block fed to the LLM cost Q&A console — deterministic figures only."""

    def test_context_lists_months_driver_and_services(self):
        from utils.cost_report import format_cost_context

        months = [(date(2026, 6, 1), 20.0), (date(2026, 7, 1), 11.82)]
        conn = MagicMock()
        cur = conn.cursor.return_value
        # cost_analyst: fetchone x2 (latest, last30), fetchall x3 (months, svcs,
        # peak drivers); then format_cost_context's own by-service fetchall.
        cur.fetchone.side_effect = [(date(2026, 7, 18),), (16.5,)]
        cur.fetchall.side_effect = [
            months,
            [("EC2 - Other", 25.0)],
            [("EC2 - Other", 11.0)],
            [("EC2 - Other", 25.0), ("AWS WAF", 6.82)],
        ]
        ctx = format_cost_context(conn)
        self.assertIn("Jul 2026: $11.82", ctx)
        self.assertIn("PARTIAL month", ctx)
        self.assertIn("Top cost driver", ctx)
        self.assertIn("EC2 - Other: $25.00", ctx)
        self.assertIn("Cost by service", ctx)


class TestFormatCostStatement(unittest.TestCase):
    def test_markdown_contains_totals_and_services(self):
        report = {
            "granularity": "month",
            "period": {"start": "2026-06-01", "end": "2026-06-30", "label": "June 2026"},
            "total_usd": 19.96,
            "prev_total_usd": 20.76,
            "delta_usd": -0.8,
            "delta_pct": -3.9,
            "daily_avg_usd": 0.67,
            "annual_run_rate_usd": 243.0,
            "complete": True,
            "services": [{"service": "AWS WAF", "usd": 8.0, "pct": 40.0}],
            "last_data_date": "2026-07-18",
        }
        text = format_cost_statement(report)
        self.assertIn("Cloud Cost Statement (June 2026)", text)
        self.assertIn("Total cost: 19.96 USD", text)
        self.assertIn("AWS WAF: 8.00 USD (40%)", text)
        self.assertIn("Annual run-rate: 243.00 USD", text)


if __name__ == "__main__":
    unittest.main()
