#!/usr/bin/env python3
"""
Tests for the /setup onboarding endpoints (routes/setup.py).

Three layers:
1. Input validation — bad region/ARN formats and incoherent
   combinations are rejected before any AWS call.
2. Test endpoint — success path returns the source identity and the
   assumed role; botocore failures surface as a 400 with the AWS
   message, never a 500.
3. Save endpoint — writes BOTH env files (root .env and ui/.env),
   chmods them to 600, applies the values to the process and never
   persists anything when the connection test fails.

boto3 is mocked throughout — no test here talks to AWS.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from schemas import AwsSetupRequest
from routes import setup as setup_module
from routes.setup import _account_id, _validation_error, _write_env_files

VALID_ROLE = "arn:aws:iam::123456789012:role/wasteless-readonly"


def _payload(**overrides):
    base = {"region": "eu-west-1", "role_arn": VALID_ROLE}
    base.update(overrides)
    return AwsSetupRequest(**base)


def _fake_session(account="123456789012", assume_error=None):
    """boto3.Session whose STS client answers get_caller_identity and
    assume_role without network."""
    sts = MagicMock()
    sts.get_caller_identity.return_value = {
        "Arn": f"arn:aws:iam::{account}:user/installer",
        "Account": account,
    }
    if assume_error is not None:
        sts.assume_role.side_effect = assume_error
    session = MagicMock()
    session.client.return_value = sts
    return session


class TestValidation(unittest.TestCase):

    def test_valid_role_payload(self):
        self.assertIsNone(_validation_error(_payload()))

    def test_valid_keys_payload(self):
        p = _payload(role_arn="", access_key_id="AKIAXXXX", secret_access_key="s3cret")
        self.assertIsNone(_validation_error(p))

    def test_bad_region(self):
        self.assertIn("region", _validation_error(_payload(region="Paris")))

    def test_bad_role_arn(self):
        self.assertIn("ARN", _validation_error(_payload(role_arn="role/not-an-arn")))

    def test_write_role_requires_read_role(self):
        p = _payload(
            role_arn="", write_role_arn=VALID_ROLE, access_key_id="AKIA", secret_access_key="s"
        )
        self.assertIn("read-only", _validation_error(p))

    def test_key_without_secret(self):
        p = _payload(access_key_id="AKIAXXXX")
        self.assertIn("secret", _validation_error(p))

    def test_nothing_provided(self):
        p = AwsSetupRequest()
        self.assertIn("provide", _validation_error(p))


class TestAccountId(unittest.TestCase):
    """_account_id feeds the pre-filled ARNs: env var first, root .env as
    fallback (installs whose install.sh predates the ui/.env mirror), and
    anything that is not a 12-digit ID is treated as absent."""

    def test_from_environment(self):
        with patch.dict(setup_module.os.environ, {"AWS_ACCOUNT_ID": "123456789012"}):
            self.assertEqual(_account_id(), "123456789012")

    def test_invalid_value_is_ignored(self):
        with patch.dict(setup_module.os.environ, {"AWS_ACCOUNT_ID": "not-an-id"}):
            self.assertEqual(_account_id(), "")

    def test_fallback_to_root_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text("DB_HOST=x\nAWS_ACCOUNT_ID=999988887777\n")
            with (
                patch.dict(setup_module.os.environ, {}, clear=False),
                patch.object(setup_module, "ROOT_DIR", Path(tmp)),
            ):
                setup_module.os.environ.pop("AWS_ACCOUNT_ID", None)
                self.assertEqual(_account_id(), "999988887777")


class TestEndpoints(unittest.TestCase):
    """FastAPI TestClient against the real app (needs Postgres for the
    import-time pool, same convention as the other route tests)."""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from main import app
        except Exception as e:
            raise unittest.SkipTest(f"app non importable ({e})") from e
        cls.client = TestClient(app)

    def test_get_setup_page(self):
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Connect your AWS account", resp.text)
        # The quick-create link always ships with the page
        self.assertIn("wasteless-onboarding.yaml", resp.text)

    def test_setup_page_prefills_suggested_arns(self):
        with patch.dict(setup_module.os.environ, {"AWS_ACCOUNT_ID": "123456789012"}, clear=False):
            setup_module.os.environ.pop("AWS_ROLE_ARN", None)
            setup_module.os.environ.pop("AWS_WRITE_ROLE_ARN", None)
            resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("arn:aws:iam::123456789012:role/wasteless-readonly", resp.text)
        self.assertIn("arn:aws:iam::123456789012:role/wasteless-remediation", resp.text)

    def test_test_endpoint_validates_write_role(self):
        """A pre-filled remediation ARN that cannot be assumed must fail the
        test — saving it silently would break remediation much later."""

        def assume(RoleArn, **kwargs):
            if RoleArn.endswith("-remediation"):
                raise Exception("AccessDenied: wasteless-remediation does not exist")
            return {}

        session = _fake_session()
        session.client.return_value.assume_role.side_effect = assume
        write_arn = "arn:aws:iam::123456789012:role/wasteless-remediation"
        with patch.object(setup_module.boto3, "Session", return_value=session):
            resp = self.client.post(
                "/api/aws-setup/test",
                json={"role_arn": VALID_ROLE, "write_role_arn": write_arn},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("wasteless-remediation", resp.json()["error"])

    def test_test_endpoint_write_role_success(self):
        write_arn = "arn:aws:iam::123456789012:role/wasteless-remediation"
        with patch.object(setup_module.boto3, "Session", return_value=_fake_session()):
            resp = self.client.post(
                "/api/aws-setup/test",
                json={"role_arn": VALID_ROLE, "write_role_arn": write_arn},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["write_role_assumed"], write_arn)

    def test_test_endpoint_success(self):
        with patch.object(setup_module.boto3, "Session", return_value=_fake_session()):
            resp = self.client.post("/api/aws-setup/test", json={"role_arn": VALID_ROLE})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["account_id"], "123456789012")
        self.assertEqual(data["role_assumed"], VALID_ROLE)

    def test_test_endpoint_aws_failure_is_400(self):
        session = _fake_session(assume_error=Exception("AccessDenied: not authorized"))
        with patch.object(setup_module.boto3, "Session", return_value=session):
            resp = self.client.post("/api/aws-setup/test", json={"role_arn": VALID_ROLE})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("AccessDenied", resp.json()["error"])

    def test_test_endpoint_format_error_is_400_without_aws_call(self):
        with patch.object(setup_module.boto3, "Session") as session_cls:
            resp = self.client.post("/api/aws-setup/test", json={"role_arn": "nope"})
        self.assertEqual(resp.status_code, 400)
        session_cls.assert_not_called()

    def test_save_writes_both_env_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_env = Path(tmp) / ".env"
            ui_env = Path(tmp) / "ui.env"
            root_env.write_text("DB_HOST=localhost\nAWS_REGION=us-east-1\n")
            with (
                patch.object(setup_module, "ENV_FILES", [root_env, ui_env]),
                patch.object(setup_module.boto3, "Session", return_value=_fake_session()),
                patch.object(setup_module, "check_aws_reachable", return_value=True),
                patch.object(setup_module, "_start_background_collection", return_value=True),
            ):
                resp = self.client.post("/api/aws-setup", json={"role_arn": VALID_ROLE})
            self.assertEqual(resp.status_code, 200)
            root_text = root_env.read_text()
            # Existing keys survive, AWS_REGION is replaced in place
            self.assertIn("DB_HOST=localhost", root_text)
            self.assertEqual(root_text.count("AWS_REGION="), 1)
            self.assertIn("AWS_REGION=eu-west-1", root_text)
            self.assertIn(f"AWS_ROLE_ARN={VALID_ROLE}", root_text)
            # ui/.env created from scratch with the same values
            self.assertIn(f"AWS_ROLE_ARN={VALID_ROLE}", ui_env.read_text())
            # Secrets-grade permissions on both
            self.assertEqual(root_env.stat().st_mode & 0o777, 0o600)
            self.assertEqual(ui_env.stat().st_mode & 0o777, 0o600)

    def test_save_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_env = Path(tmp) / ".env"
            ui_env = Path(tmp) / "ui.env"
            session = _fake_session(assume_error=Exception("AccessDenied"))
            with (
                patch.object(setup_module, "ENV_FILES", [root_env, ui_env]),
                patch.object(setup_module.boto3, "Session", return_value=session),
            ):
                resp = self.client.post("/api/aws-setup", json={"role_arn": VALID_ROLE})
            self.assertEqual(resp.status_code, 400)
            self.assertFalse(root_env.exists())
            self.assertFalse(ui_env.exists())


class TestPostSaveCollection(unittest.TestCase):
    """A successful save fires one background collection (fire-and-forget);
    its failure must never fail the save itself."""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from main import app
        except Exception as e:
            raise unittest.SkipTest(f"app non importable ({e})") from e
        cls.client = TestClient(app)

    def _save(self, collection_mock):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(setup_module, "ENV_FILES", [Path(tmp) / ".env", Path(tmp) / "ui.env"]),
                patch.object(setup_module.boto3, "Session", return_value=_fake_session()),
                patch.object(setup_module, "check_aws_reachable", return_value=True),
                patch.object(setup_module, "_start_background_collection", collection_mock),
            ):
                return self.client.post("/api/aws-setup", json={"role_arn": VALID_ROLE})

    def test_save_starts_collection_and_reports_it(self):
        collection = MagicMock(return_value=True)
        resp = self._save(collection)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["collection_started"])
        collection.assert_called_once()

    def test_save_succeeds_when_collection_cannot_start(self):
        collection = MagicMock(return_value=False)
        resp = self._save(collection)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertFalse(resp.json()["collection_started"])

    def test_failed_save_does_not_collect(self):
        collection = MagicMock(return_value=True)
        with (
            patch.object(
                setup_module.boto3,
                "Session",
                return_value=_fake_session(assume_error=Exception("AccessDenied")),
            ),
            patch.object(setup_module, "_start_background_collection", collection),
        ):
            resp = self.client.post("/api/aws-setup", json={"role_arn": VALID_ROLE})
        self.assertEqual(resp.status_code, 400)
        collection.assert_not_called()

    def test_start_background_collection_delegates_to_shared_util(self):
        # Launch behavior itself is covered in test_collect_now.py, the
        # implementation moved to utils/collect.py (shared with the
        # Collect now button).
        with patch.object(setup_module, "start_background_collection", return_value=True) as sbc:
            self.assertTrue(setup_module._start_background_collection())
        sbc.assert_called_once_with(setup_module.ROOT_DIR)


class TestWriteEnvFiles(unittest.TestCase):

    def test_empty_values_are_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("AWS_EXTERNAL_ID=keep-me\n")
            with patch.object(setup_module, "ENV_FILES", [env]):
                _write_env_files({"AWS_EXTERNAL_ID": "", "AWS_REGION": "eu-west-1"})
            text = env.read_text()
            # Empty submission never erases an existing value
            self.assertIn("AWS_EXTERNAL_ID=keep-me", text)
            self.assertIn("AWS_REGION=eu-west-1", text)


if __name__ == "__main__":
    unittest.main()
