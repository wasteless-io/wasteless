#!/usr/bin/env python3
"""The Cloud Resource Inventory tagging endpoint: it must write the tag to
AWS through the write role, record the action in actions_log, and refuse the
reserved 'aws:' key namespace. AWS is faked with moto; the DB connection is
a mock (we only assert the audit insert fired)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import boto3
    from moto import mock_aws
    from fastapi.testclient import TestClient

    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(HAVE, "moto / fastapi.testclient not installed")
class TestTagEndpoint(unittest.TestCase):
    def _fake_db(self):
        conn = MagicMock()
        return conn

    def test_tags_instance_and_writes_audit_log(self):
        from main import app
        from state import get_db
        import utils.aws_clients as awsc

        with mock_aws():
            # Create the instance through the SAME moto account the endpoint
            # will use (both go through a plain boto3 client, no creds → one
            # default moto backend).
            ec2 = boto3.client("ec2", region_name="eu-west-1")
            iid = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)["Instances"][0][
                "InstanceId"
            ]

            conn = self._fake_db()
            app.dependency_overrides[get_db] = lambda: conn

            def fake_get_client(service, region=None, write=False, **kw):
                return boto3.client(service, region_name=region)

            try:
                with patch.object(awsc, "get_client", fake_get_client):
                    resp = TestClient(app).post(
                        "/api/cloud-resources/tag",
                        json={
                            "resources": [{"id": iid, "region": "eu-west-1"}],
                            "key": "CostCenter",
                            "value": "team-x",
                        },
                    )
            finally:
                app.dependency_overrides.pop(get_db, None)

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["tagged"], 1)
            # The tag really landed on the instance.
            tags = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0].get(
                "Tags", []
            )
            self.assertIn({"Key": "CostCenter", "Value": "team-x"}, tags)
            # And the action was written to the audit log.
            self.assertTrue(conn.cursor.return_value.execute.called)
            conn.commit.assert_called()

    def test_reserved_aws_key_is_rejected(self):
        from main import app
        from state import get_db

        conn = self._fake_db()
        app.dependency_overrides[get_db] = lambda: conn
        try:
            resp = TestClient(app).post(
                "/api/cloud-resources/tag",
                json={
                    "resources": [{"id": "i-abc", "region": "eu-west-1"}],
                    "key": "aws:managed",
                    "value": "x",
                },
            )
        finally:
            app.dependency_overrides.pop(get_db, None)
        self.assertEqual(resp.status_code, 422)
        # No AWS call, no audit row.
        self.assertFalse(conn.cursor.return_value.execute.called)


class TestTagCardMarkup(unittest.TestCase):
    """Pure template check — no AWS/DB, always runs."""

    def test_tag_card_and_checkboxes_present(self):
        tpl = (
            Path(__file__).resolve().parents[1] / "templates" / "cloud_resources.html"
        ).read_text()
        self.assertIn('class="card tag-card"', tpl)
        self.assertIn('class="tag-check"', tpl)
        self.assertIn("applyTags()", tpl)
        self.assertIn("/api/cloud-resources/tag", tpl)
        # Existing tags are surfaced back in a dedicated column.
        self.assertIn(">Tags</th>", tpl)
        self.assertIn('class="tag-chip"', tpl)


if __name__ == "__main__":
    unittest.main()
