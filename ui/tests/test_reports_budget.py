#!/usr/bin/env python3
"""Reports CFO lens: budget backend (helpers + endpoint), the calendar-month
gate in the route, and the lens/exec markup on the template. The DB is a
mock throughout; no AWS, no real Postgres."""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.budget import get_budget, set_budget, month_spend_usd


class TestBudgetHelpers(unittest.TestCase):
    def _conn(self, fetch=None):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = fetch
        return conn

    def test_get_budget_none_when_empty(self):
        self.assertIsNone(get_budget(self._conn(fetch=None)))

    def test_get_budget_reads_latest(self):
        self.assertEqual(get_budget(self._conn(fetch=(45000.0,))), 45000.0)

    def test_get_budget_dict_row(self):
        self.assertEqual(get_budget(self._conn(fetch={"monthly_usd": 12000.0})), 12000.0)

    def test_set_budget_inserts_and_commits(self):
        conn = self._conn()
        set_budget(conn, 30000.0, updated_by="reports_ui")
        args = conn.cursor.return_value.execute.call_args[0]
        self.assertIn("INSERT INTO budget_settings", args[0])
        self.assertEqual(args[1], (30000.0, "reports_ui"))
        conn.commit.assert_called_once()

    def test_month_spend_bounds_are_exclusive_next_month(self):
        conn = self._conn(fetch=(1234.5,))
        self.assertEqual(month_spend_usd(conn, 2026, 7), 1234.5)
        params = conn.cursor.return_value.execute.call_args[0][1]
        self.assertEqual(params, (date(2026, 7, 1), date(2026, 8, 1)))

    def test_month_spend_december_rolls_to_next_year(self):
        conn = self._conn(fetch=(0,))
        month_spend_usd(conn, 2026, 12)
        params = conn.cursor.return_value.execute.call_args[0][1]
        self.assertEqual(params, (date(2026, 12, 1), date(2027, 1, 1)))


class TestBudgetFor(unittest.TestCase):
    """_budget_for frames the monthly budget against the cost total, and only
    for a month-granularity report."""

    def _budget(self, granularity, total, amount=45000.0):
        from routes.reports import _budget_for

        report = {
            "granularity": granularity,
            "total_usd": total,
            "period": {"label": "June 2026"},
        }
        with patch("utils.budget.get_budget", return_value=amount):
            return _budget_for(MagicMock(), report)

    def test_month_uses_cost_total_as_actual(self):
        b = self._budget("month", 19.96)
        self.assertTrue(b["is_month"])
        self.assertEqual(b["actual"], 19.96)
        self.assertEqual(b["amount"], 45000.0)

    def test_non_month_has_no_actual(self):
        b = self._budget("year", 172.69)
        self.assertFalse(b["is_month"])
        self.assertIsNone(b["actual"])


class TestBudgetEndpoint(unittest.TestCase):
    def test_post_sets_budget_and_rejects_negative(self):
        from fastapi.testclient import TestClient
        from main import app
        from state import get_db

        conn = MagicMock()
        app.dependency_overrides[get_db] = lambda: conn
        try:
            client = TestClient(app)
            ok = client.post("/api/reports/budget", json={"monthly_usd": 45000})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["monthly_usd"], 45000)
            self.assertTrue(conn.cursor.return_value.execute.called)

            bad = client.post("/api/reports/budget", json={"monthly_usd": -1})
            self.assertEqual(bad.status_code, 422)
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestReportsMarkup(unittest.TestCase):
    """Template checks. /reports is the on-screen draft/preview of one shared
    document (_report_document.html); the PDF is served standalone at
    /reports/print. Both render the same partial."""

    def _tpl(self, name):
        return (Path(__file__).resolve().parents[1] / "templates" / name).read_text()

    def test_reports_is_document_preview(self):
        tpl = self._tpl("reports.html")
        # Renders the shared document partial (screen == PDF) inside a sheet.
        self.assertIn('{% include "_report_document.html" %}', tpl)
        self.assertIn("doc-sheet", tpl)
        # Export PDF is the primary action; granularity + budget are controls.
        self.assertIn("/reports/print", tpl)
        self.assertIn("Export PDF", tpl)
        self.assertIn("/api/reports/budget", tpl)
        self.assertIn("g={{ gg }}", tpl)

    def test_shared_document_is_cost_statement(self):
        tpl = self._tpl("_report_document.html")
        # Accounting view of cost, not a waste report.
        self.assertIn("Cloud Cost Statement", tpl)
        self.assertIn("Cost by service", tpl)
        self.assertIn("Cost trend", tpl)
        self.assertIn("run-rate", tpl)
        self.assertNotIn("Identified waste", tpl)
        self.assertNotIn("Governance", tpl)

    def test_standalone_print_template(self):
        tpl = self._tpl("reports_print.html")
        # Self-contained document: its own <html>, no base.html extend.
        self.assertIn("<!doctype html>", tpl.lower())
        self.assertNotIn("{% extends", tpl)
        # Composes the shared document + shared css, not a private copy.
        self.assertIn('{% include "_report_document.html" %}', tpl)
        self.assertIn('{% include "_report_document_css.html" %}', tpl)


if __name__ == "__main__":
    unittest.main()
