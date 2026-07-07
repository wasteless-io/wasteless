#!/usr/bin/env python3
"""
Regression tests: /history, and the Scheduled/PR-open sections of
/recommendations, must say when their table is truncated rather than
silently implying the rows shown are everything.

Before this fix, the header badge/count always showed len(displayed_rows)
— capped at 100 (history) or 100 (scheduled/pr_open) — with no indication
that more rows matched the filters. Mirrors the fix already applied to
/recommendations' main "Savings" total (past 500 pending rows).
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


@unittest.skipUnless(
    PSYCOPG2_AVAILABLE and TESTCLIENT_AVAILABLE, "psycopg2 or fastapi.testclient not installed"
)
class TestPageTruncationTotals(unittest.TestCase):

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
            raise unittest.SkipTest(f"Postgres indisponible ({e})")

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

    def test_history_shows_true_total_past_the_100_row_cap(self):
        self.cur.execute("""
            INSERT INTO actions_log (
                resource_id, resource_type, action_type, action_status,
                dry_run, action_date
            )
            SELECT 'i-trunc-' || g, 'ec2_instance', 'test_trunc_action',
                   'success', true, NOW()
            FROM generate_series(1, 105) AS g
        """)

        resp = self.client.get(
            "/history", params={"action_filter": "test_trunc_action", "days_back": 1}
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("105", resp.text)
        self.assertIn("100 of 105", resp.text)

    def test_scheduled_and_pr_open_show_true_total_past_the_100_row_cap(self):
        self.cur.execute("""
            INSERT INTO waste_detected (
                detection_date, provider, account_id, resource_id,
                resource_type, waste_type, monthly_waste_eur,
                confidence_score, metadata, created_at
            )
            SELECT CURRENT_DATE, 'aws', 'test-trunc-recs',
                   'test-trunc-sched-' || g, 'ec2_instance', 'test_waste',
                   10.0, 0.9, '{}'::jsonb, NOW()
            FROM generate_series(1, 105) AS g
            RETURNING id
        """)
        waste_ids = [row["id"] for row in self.cur.fetchall()]
        self.cur.execute(
            """
            INSERT INTO recommendations (waste_id, recommendation_type, status,
                                          estimated_monthly_savings_eur,
                                          execute_after)
            SELECT id, 'stop_instance', 'scheduled', 10.0, NOW() + INTERVAL '3 days'
            FROM waste_detected WHERE id = ANY(%s)
        """,
            (waste_ids,),
        )

        resp = self.client.get("/recommendations")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("100 of 105", resp.text)


if __name__ == "__main__":
    unittest.main()
