"""Anti-CSRF/DNS-rebinding middleware (utils.security).

Write methods must target a trusted Host and, when a browser sends an
Origin, it must name a trusted host too. GET stays open, no-Origin clients
(curl, scripts, this test client) stay accepted. The probe endpoint is
POST /api/aws-setup/test with an empty payload: it fails validation with a
400 *after* the middleware, so 400 = passed through, 403 = blocked.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from utils import security


class TestHostname(unittest.TestCase):

    def test_host_with_port(self):
        self.assertEqual(security.hostname("localhost:8888"), "localhost")

    def test_origin_url(self):
        self.assertEqual(security.hostname("http://evil.com:8888"), "evil.com")

    def test_bracketed_ipv6(self):
        self.assertEqual(security.hostname("[::1]:8888"), "::1")

    def test_bare_ipv6(self):
        self.assertEqual(security.hostname("::1"), "::1")

    def test_case_insensitive(self):
        self.assertEqual(security.hostname("LOCALHOST:8888"), "localhost")


class TestTrustedHosts(unittest.TestCase):

    def test_loopback_always_trusted(self):
        trusted = security.trusted_write_hosts()
        self.assertIn("localhost", trusted)
        self.assertIn("127.0.0.1", trusted)

    def test_extra_hosts_from_env(self):
        with patch.dict(
            security.os.environ,
            {"WASTELESS_TRUSTED_HOSTS": "proxy.example.com, other.example.com:443"},
        ):
            trusted = security.trusted_write_hosts()
        self.assertIn("proxy.example.com", trusted)
        self.assertIn("other.example.com", trusted)

    def test_wildcard_bind_is_not_a_trusted_host(self):
        # S104: pas un bind — le test verifie justement l'exclusion du joker
        with patch.dict(security.os.environ, {"WASTELESS_HOST": "0.0.0.0"}):  # noqa: S104
            self.assertNotIn("0.0.0.0", security.trusted_write_hosts())  # noqa: S104


class TestMiddleware(unittest.TestCase):

    PROBE = "/api/aws-setup/test"

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from main import app
        except Exception as e:
            raise unittest.SkipTest(f"app non importable ({e})") from e
        cls.client = TestClient(app)

    def test_post_without_origin_passes(self):
        # curl/scripts locaux : pas d'Origin, Host testserver (de confiance)
        resp = self.client.post(self.PROBE, json={})
        self.assertEqual(resp.status_code, 400)  # validation, pas le middleware

    def test_post_with_trusted_origin_passes(self):
        resp = self.client.post(self.PROBE, json={}, headers={"Origin": "http://localhost:8888"})
        self.assertEqual(resp.status_code, 400)

    def test_post_with_foreign_origin_blocked(self):
        resp = self.client.post(self.PROBE, json={}, headers={"Origin": "http://evil.com"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("cross-origin", resp.json()["error"])

    def test_post_with_null_origin_blocked(self):
        resp = self.client.post(self.PROBE, json={}, headers={"Origin": "null"})
        self.assertEqual(resp.status_code, 403)

    def test_post_with_foreign_host_blocked(self):
        # DNS rebinding : le domaine de l'attaquant resout vers 127.0.0.1,
        # le navigateur envoie son Host a lui — refus meme sans Origin.
        resp = self.client.post(self.PROBE, json={}, headers={"Host": "evil.example.com:8888"})
        self.assertEqual(resp.status_code, 403)

    def test_post_with_trusted_extra_host_passes(self):
        with patch.dict(security.os.environ, {"WASTELESS_TRUSTED_HOSTS": "proxy.example.com"}):
            resp = self.client.post(
                self.PROBE,
                json={},
                headers={
                    "Host": "proxy.example.com",
                    "Origin": "https://proxy.example.com",
                },
            )
        self.assertEqual(resp.status_code, 400)

    def test_get_with_foreign_origin_passes(self):
        # Lecture seule : le middleware ne s'applique qu'aux ecritures
        resp = self.client.get("/setup", headers={"Origin": "http://evil.com"})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
