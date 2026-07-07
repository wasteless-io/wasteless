#!/usr/bin/env python3
"""
Regression tests for the reject/dismiss status guard in POST /api/actions
(ui/main.py).

Before this fix, "reject" and "dismiss" updated recommendations.status
unconditionally (WHERE id = %s, no status check) — unlike every other
transition in this endpoint (cancel requires 'scheduled', grace scheduling
requires 'pending'). The UI only ever shows these buttons for 'pending'
rows, so it was unreachable in practice, but a direct API call (or a
future UI change reusing the action on the scheduled/pr_open tables)
could silently overwrite a resolved status — e.g. flipping an 'approved'
or 'applied' recommendation back to 'rejected', which is NOT excluded
from the active_waste view, making an already-remediated resource
reappear as active waste.

Uses a real Postgres connection via FastAPI's dependency override; rows
are cleaned up explicitly since the route commits internally.
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


ACCOUNT_ID = "test-action-guards"


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
class TestActionStatusGuards(unittest.TestCase):

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
        self.cur.execute(
            """
            DELETE FROM recommendations WHERE waste_id IN (
                SELECT id FROM waste_detected WHERE account_id = %s
            )
        """,
            (ACCOUNT_ID,),
        )
        self.cur.execute("DELETE FROM waste_detected WHERE account_id = %s", (ACCOUNT_ID,))
        self.conn.commit()

    def _insert_rec(self, resource_id, status):
        self.cur.execute(
            """
            INSERT INTO waste_detected (
                detection_date, provider, account_id, resource_id,
                resource_type, waste_type, monthly_waste_eur,
                confidence_score, metadata, created_at
            ) VALUES (CURRENT_DATE, 'aws', %s, %s, 'ec2_instance',
                       'test_waste', 10.0, 0.90, '{}'::jsonb, NOW())
            RETURNING id
        """,
            (ACCOUNT_ID, resource_id),
        )
        waste_id = self.cur.fetchone()["id"]
        self.cur.execute(
            """
            INSERT INTO recommendations (
                waste_id, recommendation_type, status, estimated_monthly_savings_eur
            ) VALUES (%s, 'stop_instance', %s, 10.0)
            RETURNING id
        """,
            (waste_id, status),
        )
        rec_id = self.cur.fetchone()["id"]
        self.conn.commit()
        return rec_id

    def _status(self, rec_id):
        self.cur.execute("SELECT status FROM recommendations WHERE id = %s", (rec_id,))
        return self.cur.fetchone()["status"]

    def test_reject_pending_succeeds(self):
        rec_id = self._insert_rec("i-guard-pending-reject", status="pending")
        resp = self.client.post(
            "/api/actions", json={"recommendation_ids": [rec_id], "action": "reject"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["results"][0]["success"])
        self.assertEqual(self._status(rec_id), "rejected")

    def test_reject_approved_is_rejected_by_guard(self):
        """An already-approved recommendation must not flip back to rejected."""
        rec_id = self._insert_rec("i-guard-approved-reject", status="approved")
        resp = self.client.post(
            "/api/actions", json={"recommendation_ids": [rec_id], "action": "reject"}
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        self.assertFalse(result["success"])
        self.assertEqual(self._status(rec_id), "approved")

    def test_dismiss_applied_is_rejected_by_guard(self):
        """An already-applied recommendation must not become dismissed."""
        rec_id = self._insert_rec("i-guard-applied-dismiss", status="applied")
        resp = self.client.post(
            "/api/actions", json={"recommendation_ids": [rec_id], "action": "dismiss"}
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        self.assertFalse(result["success"])
        self.assertEqual(self._status(rec_id), "applied")

    def test_dismiss_pending_succeeds(self):
        rec_id = self._insert_rec("i-guard-pending-dismiss", status="pending")
        resp = self.client.post(
            "/api/actions", json={"recommendation_ids": [rec_id], "action": "dismiss"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["results"][0]["success"])
        self.assertEqual(self._status(rec_id), "dismissed")


if __name__ == "__main__":
    unittest.main()
