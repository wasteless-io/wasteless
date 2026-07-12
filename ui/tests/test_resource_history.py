"""Resource-history endpoint (CloudTrail LookupEvents per recommendation).

boto3 is mocked; the DB dependency is overridden with a canned cursor so no
Postgres rows are needed. Covered: happy path (events mapped and cached),
missing permission (graceful hint, never a 500), unknown recommendation.
"""

import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from routes import recommendations as rec_module


def _fake_conn(row):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _cloudtrail_client(events=None, error=None):
    client = MagicMock()
    if error is not None:
        client.lookup_events.side_effect = error
    else:
        client.lookup_events.return_value = {"Events": events or []}
    return client


class TestResourceHistory(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from main import app
            from state import get_db
        except Exception as e:
            raise unittest.SkipTest(f"app non importable ({e})") from e
        cls.app = app
        # staticmethod : stocker la fonction nue en attribut de classe en
        # ferait une methode liee via self.get_db — et la cle du
        # dependency_overrides ne correspondrait plus jamais a state.get_db.
        cls.get_db = staticmethod(get_db)
        cls.client = TestClient(app)

    def setUp(self):
        rec_module._HISTORY_CACHE.clear()

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def _override_db(self, row):
        self.app.dependency_overrides[self.get_db] = lambda: _fake_conn(row)

    def test_events_are_mapped_and_returned(self):
        self._override_db({"resource_id": "vol-1", "metadata": {"region": "eu-west-3"}})
        ct = _cloudtrail_client(
            events=[
                {
                    "EventTime": datetime.datetime(2026, 2, 8, 8, 33),
                    "EventName": "CreateVolume",
                    "Username": "alice",
                }
            ]
        )
        with patch("utils.aws_clients.get_client", return_value=ct) as gc:
            resp = self.client.get("/api/recommendations/1/resource-history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["available"])
        self.assertEqual(data["region"], "eu-west-3")
        self.assertEqual(data["events"][0]["name"], "CreateVolume")
        self.assertEqual(data["events"][0]["username"], "alice")
        gc.assert_called_once_with("cloudtrail", region="eu-west-3")

    def test_result_is_cached_per_resource(self):
        self._override_db({"resource_id": "vol-cache", "metadata": {}})
        ct = _cloudtrail_client(events=[])
        with patch("utils.aws_clients.get_client", return_value=ct):
            self.client.get("/api/recommendations/1/resource-history")
            self.client.get("/api/recommendations/1/resource-history")
        # LookupEvents est limite a ~2 req/s : un seul appel AWS pour deux hits
        ct.lookup_events.assert_called_once()

    def test_access_denied_degrades_with_hint(self):
        """Stack d'onboarding anterieure a la permission : reponse 200 avec
        un mode d'emploi, jamais une 500."""
        self._override_db({"resource_id": "vol-old", "metadata": {}})
        ct = _cloudtrail_client(
            error=Exception("AccessDeniedException: not authorized to LookupEvents")
        )
        with patch("utils.aws_clients.get_client", return_value=ct):
            resp = self.client.get("/api/recommendations/1/resource-history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["available"])
        self.assertIn("cloudtrail:LookupEvents", data["hint"])
        self.assertIn("onboarding", data["hint"])

    def test_unknown_recommendation_is_404(self):
        self._override_db(None)
        resp = self.client.get("/api/recommendations/999999/resource-history")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
