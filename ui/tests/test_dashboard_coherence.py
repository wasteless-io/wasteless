#!/usr/bin/env python3
"""
Coherence tests for /dashboard: every figure is rendered in more than one
block (KPI row, control loop, Next best action), and each block computes it
independently in the template. These tests reconcile the rendered page
against the database truth, so a refactor of one block cannot silently
diverge from the others.

Follows the live-Postgres pattern of test_page_truncation_totals.py:
fixtures are inserted in a transaction and rolled back, the suite skips
when no database is reachable.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    from fastapi.testclient import TestClient

    TESTCLIENT_AVAILABLE = True
except ImportError:
    TESTCLIENT_AVAILABLE = False


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "wasteless"),
        user=os.getenv("DB_USER", "wasteless"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=5,
        cursor_factory=RealDictCursor,
    )


def eur(v):
    """Mirror of the dashboard template's money macro (without the markup)."""
    v = float(v or 0)
    if 0 < v < 0.01:
        return "<$0.01"
    if v < 100:
        return f"${v:.2f}"
    return f"${v:,.0f}"


@unittest.skipUnless(
    PSYCOPG2_AVAILABLE and TESTCLIENT_AVAILABLE, "psycopg2 or fastapi.testclient not installed"
)
class TestDashboardCoherence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        try:
            cls.conn = _connect()
        except Exception as e:
            raise unittest.SkipTest(f"Postgres indisponible ({e})") from e

        from main import app
        from state import get_db

        def _override_get_db():
            yield cls.conn

        app.dependency_overrides[get_db] = _override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        from main import app

        app.dependency_overrides.clear()
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()

    # -- helpers ---------------------------------------------------------

    def _seed_waste(self, resource_id, monthly_eur, status="pending", confidence=0.95):
        """One waste_detected row + its recommendation, returns (waste_id, reco_id)."""
        self.cur.execute(
            """
            INSERT INTO waste_detected (
                detection_date, provider, account_id, resource_id,
                resource_type, waste_type, monthly_waste_eur,
                confidence_score, metadata
            ) VALUES (CURRENT_DATE, 'aws', 'test', %s,
                      'ec2_instance', 'idle_instance', %s, %s, '{}')
            RETURNING id
        """,
            (resource_id, monthly_eur, confidence),
        )
        waste_id = self.cur.fetchone()["id"]
        self.cur.execute(
            """
            INSERT INTO recommendations (
                waste_id, recommendation_type, action_required,
                estimated_monthly_savings_eur, status
            ) VALUES (%s, 'stop_instance', 'Stop test instance', %s, %s)
            RETURNING id
        """,
            (waste_id, monthly_eur, status),
        )
        return waste_id, self.cur.fetchone()["id"]

    def _page_body(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        # Strip the <style> block: CSS comments repeat the section names
        return resp.text.split("</style>")[-1]

    # -- tests -----------------------------------------------------------

    def test_monthly_waste_kpi_matches_detected_stage(self):
        self._seed_waste("i-coherence-detected", 4321.55)

        self.cur.execute("SELECT COALESCE(SUM(monthly_waste_eur), 0) AS s FROM active_waste")
        expected = eur(self.cur.fetchone()["s"])

        body = self._page_body()
        self.assertGreaterEqual(
            body.count(expected),
            2,
            f"{expected} should appear in both the Monthly Waste KPI and the "
            f"Detected stage of the control loop",
        )

    def test_recoverable_now_matches_awaiting_review_stage(self):
        self._seed_waste("i-coherence-pending", 2233.44)

        self.cur.execute(
            "SELECT COALESCE(SUM(estimated_monthly_savings_eur), 0) AS s, COUNT(*) AS n "
            "FROM recommendations WHERE status = 'pending'"
        )
        row = self.cur.fetchone()
        expected = eur(row["s"])

        body = self._page_body()
        self.assertGreaterEqual(
            body.count(expected),
            2,
            f"{expected} should appear in both the Recoverable Now KPI and the "
            f"Awaiting-review stage of the control loop",
        )
        self.assertIn(f"{row['n']} recommendation", body)

    def test_verified_savings_is_a_monthly_rate_with_accuracy(self):
        self.cur.execute("""
            INSERT INTO savings_realized (
                resource_id, resource_type,
                measurement_start_date, measurement_end_date,
                cost_before_eur, cost_after_eur,
                actual_savings_eur, estimated_savings_eur
            ) VALUES ('i-coherence-verified', 'ec2_instance',
                      CURRENT_DATE - 30, CURRENT_DATE,
                      100, 20, 3080.00, 3500.00)
        """)

        self.cur.execute(
            "SELECT COALESCE(SUM(actual_savings_eur), 0) AS a, "
            "COALESCE(SUM(estimated_savings_eur), 0) AS e FROM savings_realized"
        )
        row = self.cur.fetchone()
        expected = eur(row["a"])
        accuracy = f"{float(row['a']) / float(row['e']) * 100:.0f}% of estimate"

        body = self._page_body()
        self.assertGreaterEqual(
            body.count(expected),
            2,
            f"{expected} should appear in both the Verified Savings KPI and the "
            f"Verified stage of the control loop",
        )
        # The KPI is labeled as a monthly rate, never a cumulative total
        self.assertIn("Verified Savings", body)
        self.assertNotIn("Since launch", body)
        self.assertIn(accuracy, body)

    def test_next_best_action_is_the_top_pending_recommendation(self):
        self._seed_waste("i-coherence-top", 9876.54, confidence=0.97)
        self._seed_waste("i-coherence-second", 55.55, confidence=0.85)

        self.cur.execute(
            "SELECT MAX(estimated_monthly_savings_eur) AS m "
            "FROM recommendations WHERE status = 'pending'"
        )
        top = eur(self.cur.fetchone()["m"])

        body = self._page_body()
        nba = body.split("Next best action")[-1]
        self.assertIn(top, nba[:1500], "the hero amount must be the highest pending savings")
        self.assertIn("97%", nba[:1500])
        self.assertIn("i-coherence-top", nba[:1500])

    def test_queued_stage_reflects_scheduled_and_manual_counts(self):
        self._seed_waste("i-coherence-sched", 150.00, status="scheduled")
        self._seed_waste("i-coherence-manual", 250.00, status="approved_manual")

        self.cur.execute(
            "SELECT COUNT(*) FILTER (WHERE status = 'scheduled') AS s, "
            "COUNT(*) FILTER (WHERE status = 'approved_manual') AS m "
            "FROM recommendations"
        )
        row = self.cur.fetchone()

        body = self._page_body()
        self.assertIn(f"{row['s']} in grace period", body)
        self.assertIn(f"{row['m']} manual to-do", body)


if __name__ == "__main__":
    unittest.main()
