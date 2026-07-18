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
import re
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

    def test_removed_blocks_stay_removed(self):
        """Blocks deleted on user request (2026-07-18) must not resurface."""
        body = self._page_body()
        self.assertNotIn("Monthly Waste", body)
        self.assertNotIn("Cost of Inaction", body)
        self.assertNotIn("Remediation control loop", body)
        self.assertNotIn("Next best action", body)
        self.assertNotIn("Cost by Resource Type", body)
        self.assertNotIn("Operational Status", body)
        # Their replacement lives on: the async Resources-by-Region card
        self.assertIn('id="regionCard"', body)

    def test_recoverable_now_matches_pending_recommendations(self):
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
            f"{expected} should appear in both the Recoverable Now KPI and its "
            f"click-through modal hero",
        )
        self.assertIn(f"{row['n']} recommendation", body)
        # Click-through modal: one row per pending decision
        self.assertIn('id="recoverableModal"', body)
        self.assertIn("i-coherence-pending", body)

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
            1,
            f"{expected} should appear in the Verified Savings KPI",
        )
        # The KPI is labeled as a monthly rate, never a cumulative total
        self.assertIn("Verified Savings", body)
        self.assertNotIn("Since launch", body)
        self.assertIn(accuracy, body)

    # Mirror of the route's Saved-so-far accrual: each real applied action
    # accrues its monthly rate until now, cut short by a restart of the
    # resource or by its proven lifetime before remediation (age_days
    # metadata, else the observation window, floor 1 day).
    SAVED_SQL = """
        WITH saving_actions AS (
            SELECT a.resource_id, a.action_date,
                   COALESCE(r.estimated_monthly_savings_eur, 0) AS monthly_rate,
                   GREATEST(
                       COALESCE((w.metadata->>'age_days')::numeric, 0),
                       EXTRACT(EPOCH FROM a.action_date - w.detection_date::timestamp)
                           / 86400.0,
                       1
                   ) AS lifetime_days
            FROM actions_log a
            LEFT JOIN recommendations r ON r.id = a.recommendation_id
            LEFT JOIN waste_detected w ON w.id = r.waste_id
            WHERE a.action_status = 'success'
              AND a.dry_run = false
              AND a.action_type <> 'start'
        ),
        accruals AS (
            SELECT s.action_date, s.monthly_rate,
                   LEAST(
                       COALESCE((SELECT MIN(u.action_date)
                                 FROM actions_log u
                                 WHERE u.resource_id = s.resource_id
                                   AND u.action_type = 'start'
                                   AND u.action_status = 'success'
                                   AND u.dry_run = false
                                   AND u.action_date > s.action_date), NOW()),
                       s.action_date + s.lifetime_days * INTERVAL '1 day'
                   ) AS accrual_end
            FROM saving_actions s
        )
        SELECT COALESCE(SUM(monthly_rate / %s
                   * EXTRACT(EPOCH FROM accrual_end - action_date) / 86400.0), 0) AS saved,
               COALESCE(SUM(monthly_rate)
                   FILTER (WHERE accrual_end > NOW() - INTERVAL '1 minute'), 0) AS live_monthly
        FROM accruals
    """

    def test_saved_so_far_matches_applied_actions_accrual(self):
        from state import DAYS_PER_MONTH

        _, reco_id = self._seed_waste("i-coherence-saved", 300.00, status="applied")
        # Detected 40 days ago -> proven lifetime (30d at action time)
        # exceeds the 10 elapsed days: the accrual runs uncapped here
        self.cur.execute(
            "UPDATE waste_detected SET detection_date = CURRENT_DATE - 40 "
            "WHERE resource_id = 'i-coherence-saved'"
        )
        self.cur.execute(
            """
            INSERT INTO actions_log (action_date, recommendation_id, resource_id,
                                     resource_type, action_type, action_status, dry_run)
            VALUES (NOW() - INTERVAL '10 days', %s, 'i-coherence-saved',
                    'ec2_instance', 'stop', 'success', false)
            """,
            (reco_id,),
        )

        # The accrual grows continuously, so bracket the render between two
        # SQL evaluations instead of expecting an exact string
        self.cur.execute(self.SAVED_SQL, (DAYS_PER_MONTH,))
        low = float(self.cur.fetchone()["saved"])
        body = self._page_body()
        self.cur.execute(self.SAVED_SQL, (DAYS_PER_MONTH,))
        high = float(self.cur.fetchone()["saved"])

        # Anchor on the card title tag: "Saved so far" also appears in
        # layout comments before the Financial Overview figures
        m = re.search(r"Saved so far</h3>.*?\$([\d,]+(?:\.\d+)?)", body, re.S)
        self.assertIsNotNone(m, "the Saved so far card should render an amount")
        shown = float(m.group(1).replace(",", ""))
        # 0.6 covers the whole-dollar display rounding above $100
        self.assertGreaterEqual(shown, low - 0.6)
        self.assertLessEqual(shown, high + 0.6)
        # A dry-run or rolled-back action must never inflate the counter:
        # the seeded action alone accrues ~10 days of $300/mo
        self.assertGreater(high, 90)
        # Click-through modal: the seeded action appears in the breakdown
        self.assertIn('id="savedModal"', body)
        self.assertIn("i-coherence-saved", body)

    def test_saved_accrual_is_capped_by_resource_lifetime(self):
        from state import DAYS_PER_MONTH

        self.cur.execute(self.SAVED_SQL, (DAYS_PER_MONTH,))
        before = self.cur.fetchone()

        # Lived 2 days before remediation, deleted 10 days ago: the credit
        # must stop at 2 days of its $300/mo rate, and the action must not
        # feed the run-rate anymore
        _, reco_id = self._seed_waste("i-coherence-capped", 300.00, status="applied")
        self.cur.execute(
            "UPDATE waste_detected SET metadata = '{\"age_days\": 2}' "
            "WHERE resource_id = 'i-coherence-capped'"
        )
        self.cur.execute(
            """
            INSERT INTO actions_log (action_date, recommendation_id, resource_id,
                                     resource_type, action_type, action_status, dry_run)
            VALUES (NOW() - INTERVAL '10 days', %s, 'i-coherence-capped',
                    'ec2_instance', 'stop', 'success', false)
            """,
            (reco_id,),
        )

        self.cur.execute(self.SAVED_SQL, (DAYS_PER_MONTH,))
        after = self.cur.fetchone()

        expected_credit = 300.00 / DAYS_PER_MONTH * 2  # ~19.7, frozen
        delta = float(after["saved"]) - float(before["saved"])
        self.assertAlmostEqual(delta, expected_credit, delta=1.0)
        self.assertAlmostEqual(
            float(after["live_monthly"]),
            float(before["live_monthly"]),
            delta=0.01,
            msg="a capped-out action must not contribute to the run-rate",
        )
        # The breakdown row states the cap, not a restart
        body = self._page_body()
        self.assertIn("lived 2d before remediation", body)

    def test_upcoming_card_lists_scheduled_executions(self):
        _, reco_id = self._seed_waste("i-coherence-upcoming", 120.00, status="scheduled")
        self.cur.execute(
            "UPDATE recommendations SET execute_after = NOW() + INTERVAL '3 days' "
            "WHERE id = %s RETURNING execute_after",
            (reco_id,),
        )
        execute_after = self.cur.fetchone()["execute_after"]

        body = self._page_body()
        self.assertIn("Upcoming", body)
        self.assertIn("i-coherence-upcoming", body)
        self.assertIn(execute_after.strftime("%b %-d, %H:%M"), body)
        # The veto window must link back to the cancellable list
        self.assertIn("Cancel window open", body)

    def test_saved_breakdown_shortens_arns_to_resource_name(self):
        arn = (
            "arn:aws:elasticloadbalancing:eu-west-1:604110133218:"
            "loadbalancer/app/coherence-test-alb/950ebb3a208c4a48"
        )
        _, reco_id = self._seed_waste("arn-holder", 50.00, status="applied")
        self.cur.execute(
            """
            INSERT INTO actions_log (action_date, recommendation_id, resource_id,
                                     resource_type, action_type, action_status, dry_run)
            VALUES (NOW() - INTERVAL '2 days', %s, %s,
                    'load_balancer', 'delete_load_balancer', 'success', false)
            """,
            (reco_id, arn),
        )

        body = self._page_body()
        # The row label is the load balancer's name; the full ARN stays
        # available in the row's title attribute
        self.assertIn(">coherence-test-alb<", body)
        self.assertIn(f'title="{arn}"', body)

    def test_total_cost_kpi_matches_cloud_costs_raw(self):
        self.cur.execute("""
            INSERT INTO cloud_costs_raw
            (provider, account_id, service, usage_date, cost, currency, region)
            VALUES ('aws', 'test', 'Coherence Test Service',
                    CURRENT_DATE - 1, 4455.66, 'USD', 'eu-west-1')
        """)

        self.cur.execute(
            "SELECT COALESCE(SUM(cost), 0) AS s, MIN(usage_date) AS f, "
            "MAX(usage_date) AS l FROM cloud_costs_raw"
        )
        row = self.cur.fetchone()
        expected = eur(row["s"])
        if row["f"] == row["l"]:
            period = row["f"].strftime("%-d %b")
        else:
            period = f"{row['f'].strftime('%-d %b')} to {row['l'].strftime('%-d %b')}"

        body = self._page_body()
        self.assertIn("Total Cost", body)
        self.assertIn(expected, body)
        # The sub-label states the exact collected window, never more
        self.assertIn(f'<div class="kpi-sub">{period}</div>', body)
        # Click-through modal: same window in the title, per-service rows
        self.assertIn(f"Total Cost · {period}", body)
        self.assertIn('id="totalCostModal"', body)
        self.assertIn("Coherence Test Service", body)
        # Monthly stacked chart renders whenever cost data exists
        self.assertIn("Cost &amp; usage by month", body)
        self.assertIn('id="costByServiceChart"', body)


if __name__ == "__main__":
    unittest.main()
