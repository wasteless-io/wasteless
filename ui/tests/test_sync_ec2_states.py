#!/usr/bin/env python3
"""
Integration tests for _sync_ec2_instance_states (ui/main.py), the helper
shared by sync_aws_job (every 5 min) and the manual /api/sync-aws button.

Regression covered: the manual button used to run its own inline copy of
this logic scoped to status = 'pending' only, while the vanished-resource
check right above it in the same function used the full SYNCABLE_STATUSES
list. Worse, the automatic job never ran this check at all — it only
detected vanished resources, so a stop_instance recommendation for an
instance stopped outside wasteless (AWS console, another tool) stayed
'scheduled'/'rejected'/'pending' forever unless a human clicked sync, and
even then only if it happened to be 'pending'. Both call sites now share
this one function, so they always resolve identically.

Uses a real Postgres connection (rolled back after each test); skipped
cleanly if the database is not reachable.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

from jobs import _sync_ec2_instance_states


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


def _fake_get_client(states_by_id):
    """A get_client stand-in reporting the given states in eu-west-1 only."""

    def factory(service, region=None):
        client = MagicMock()
        if region == "eu-west-1":
            client.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {"InstanceId": iid, "State": {"Name": state}}
                            for iid, state in states_by_id.items()
                        ]
                    }
                ]
            }
        else:
            client.describe_instances.return_value = {"Reservations": []}
        return client

    return factory


@unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
class TestSyncEc2InstanceStates(unittest.TestCase):

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

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()

    def _insert_ec2_waste(self, resource_id, status, recommendation_type="stop_instance"):
        self.cur.execute(
            """
            INSERT INTO waste_detected (
                detection_date, provider, account_id, resource_id,
                resource_type, waste_type, monthly_waste_eur,
                confidence_score, metadata, created_at
            ) VALUES (CURRENT_DATE, 'aws', 'test-sync-ec2', %s, 'ec2_instance',
                       'test_waste', 10.0, 0.90, '{}'::jsonb, NOW())
            RETURNING id
        """,
            (resource_id,),
        )
        waste_id = self.cur.fetchone()["id"]
        self.cur.execute(
            """
            INSERT INTO recommendations (
                waste_id, recommendation_type, status, estimated_monthly_savings_eur
            ) VALUES (%s, %s, %s, 10.0)
            RETURNING id
        """,
            (waste_id, recommendation_type, status),
        )
        return self.cur.fetchone()["id"]

    def _rec_status(self, rec_id):
        self.cur.execute("SELECT status FROM recommendations WHERE id = %s", (rec_id,))
        return self.cur.fetchone()["status"]

    def test_stopped_scheduled_instance_becomes_obsolete(self):
        """A 'scheduled' stop_instance rec must resolve too, not just 'pending'."""
        rec_id = self._insert_ec2_waste("i-stopped-scheduled", status="scheduled")

        with patch(
            "utils.aws_clients.get_client", _fake_get_client({"i-stopped-scheduled": "stopped"})
        ):
            synced, obsolete = _sync_ec2_instance_states(self.cur, ["i-stopped-scheduled"])

        self.assertEqual(obsolete, 1)
        self.assertEqual(synced, 0)
        self.assertEqual(self._rec_status(rec_id), "obsolete")

    def test_stopped_rejected_instance_becomes_obsolete(self):
        """Same fix applied to a 'rejected' rec (previously only 'pending')."""
        rec_id = self._insert_ec2_waste("i-stopped-rejected", status="rejected")

        with patch(
            "utils.aws_clients.get_client", _fake_get_client({"i-stopped-rejected": "stopped"})
        ):
            synced, obsolete = _sync_ec2_instance_states(self.cur, ["i-stopped-rejected"])

        self.assertEqual(obsolete, 1)
        self.assertEqual(self._rec_status(rec_id), "obsolete")

    def test_still_running_instance_stays_pending_and_syncs_state(self):
        rec_id = self._insert_ec2_waste("i-still-running", status="pending")

        with patch(
            "utils.aws_clients.get_client", _fake_get_client({"i-still-running": "running"})
        ):
            synced, obsolete = _sync_ec2_instance_states(self.cur, ["i-still-running"])

        self.assertEqual(synced, 1)
        self.assertEqual(obsolete, 0)
        self.assertEqual(self._rec_status(rec_id), "pending")

    def test_vanished_instance_becomes_obsolete(self):
        rec_id = self._insert_ec2_waste("i-gone", status="pending")

        with patch("utils.aws_clients.get_client", _fake_get_client({})):
            synced, obsolete = _sync_ec2_instance_states(self.cur, ["i-gone"])

        self.assertEqual(obsolete, 1)
        self.assertEqual(self._rec_status(rec_id), "obsolete")

    def test_dismissed_instance_is_never_touched(self):
        """dismissed is a terminal decision: sync must not overwrite it."""
        rec_id = self._insert_ec2_waste("i-dismissed", status="dismissed")

        with patch("utils.aws_clients.get_client", _fake_get_client({})):
            synced, obsolete = _sync_ec2_instance_states(self.cur, ["i-dismissed"])

        self.assertEqual(obsolete, 0)
        self.assertEqual(synced, 0)
        self.assertEqual(self._rec_status(rec_id), "dismissed")


if __name__ == "__main__":
    unittest.main()
