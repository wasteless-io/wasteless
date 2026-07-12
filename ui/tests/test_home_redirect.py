"""First-run redirect: `/` sends the user to the /setup wizard as long as
no AWS connection is configured, and behaves normally once it is. Only the
root path redirects — every other page stays directly reachable."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from routes import home as home_module


class TestFirstRunRedirect(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from main import app
        except Exception as e:
            raise unittest.SkipTest(f"app non importable ({e})") from e
        cls.client = TestClient(app)

    def test_root_redirects_to_setup_when_not_configured(self):
        with patch.object(home_module, "aws_connection_configured", return_value=False):
            resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/setup")

    def test_root_renders_home_when_configured(self):
        with patch.object(home_module, "aws_connection_configured", return_value=True):
            resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)

    def test_other_pages_never_redirect(self):
        # Le dashboard reste accessible en direct meme sans connexion AWS :
        # seule la racine emmene au wizard.
        with patch.object(home_module, "aws_connection_configured", return_value=False):
            resp = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)


class TestAwsConnectionConfigured(unittest.TestCase):
    """Heuristique volontairement large : env vars, sinon ~/.aws/credentials.
    Dans le doute, configure — on ne renvoie jamais un compte connecte
    vers /setup."""

    def _configured(self, tmp_home, **env):
        import state

        with patch.dict(state.os.environ, env, clear=False):
            for key in ("AWS_ROLE_ARN", "AWS_ACCESS_KEY_ID"):
                if key not in env:
                    state.os.environ.pop(key, None)
            with patch.object(state.Path, "home", return_value=Path(tmp_home)):
                return state.aws_connection_configured()

    def test_nothing_configured(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(self._configured(tmp))

    def test_role_arn_in_env(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(self._configured(tmp, AWS_ROLE_ARN="arn:aws:iam::123456789012:role/x"))

    def test_shared_credentials_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".aws").mkdir()
            (Path(tmp) / ".aws" / "credentials").touch()
            self.assertTrue(self._configured(tmp))


if __name__ == "__main__":
    unittest.main()
