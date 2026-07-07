"""
Unit tests for utils/notifications.py — email alert on action failure.

SMTP is always mocked: no test here ever opens a real network connection.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.notifications import notify_action_failure, _smtp_settings


class TestSmtpSettings(unittest.TestCase):
    def test_no_host_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(_smtp_settings())

    def test_defaults_applied(self):
        with patch.dict("os.environ", {"SMTP_HOST": "smtp.example.com"}, clear=True):
            settings = _smtp_settings()
            self.assertEqual(settings["host"], "smtp.example.com")
            self.assertEqual(settings["port"], 587)
            self.assertTrue(settings["use_tls"])
            self.assertEqual(settings["from_addr"], "")

    def test_from_falls_back_to_user(self):
        env = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "bot@example.com"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(_smtp_settings()["from_addr"], "bot@example.com")

    def test_explicit_from_wins(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "bot@example.com",
            "SMTP_FROM": "alerts@example.com",
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(_smtp_settings()["from_addr"], "alerts@example.com")

    def test_use_tls_can_be_disabled(self):
        env = {"SMTP_HOST": "smtp.example.com", "SMTP_USE_TLS": "false"}
        with patch.dict("os.environ", env, clear=True):
            self.assertFalse(_smtp_settings()["use_tls"])


class TestNotifyActionFailure(unittest.TestCase):
    def _config(self, **notifications):
        mock_manager = MagicMock()
        mock_manager.get_notifications.return_value = notifications
        return mock_manager

    @patch("utils.notifications.ConfigManager")
    def test_noop_when_notify_on_error_disabled(self, mock_cm_cls):
        mock_cm_cls.return_value = self._config(notify_on_error=False, email="a@b.com")
        with patch("utils.notifications.smtplib.SMTP") as mock_smtp:
            sent = notify_action_failure("stop_instance", "i-123", "boom")
        self.assertFalse(sent)
        mock_smtp.assert_not_called()

    @patch("utils.notifications.ConfigManager")
    def test_noop_when_no_recipient_configured(self, mock_cm_cls):
        mock_cm_cls.return_value = self._config(notify_on_error=True, email="")
        with patch("utils.notifications.smtplib.SMTP") as mock_smtp:
            sent = notify_action_failure("stop_instance", "i-123", "boom")
        self.assertFalse(sent)
        mock_smtp.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("utils.notifications.ConfigManager")
    def test_noop_when_smtp_not_configured(self, mock_cm_cls):
        mock_cm_cls.return_value = self._config(notify_on_error=True, email="a@b.com")
        with patch("utils.notifications.smtplib.SMTP") as mock_smtp:
            sent = notify_action_failure("stop_instance", "i-123", "boom")
        self.assertFalse(sent)
        mock_smtp.assert_not_called()

    @patch.dict(
        "os.environ", {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "bot@x.com"}, clear=True
    )
    @patch("utils.notifications.ConfigManager")
    def test_sends_when_fully_configured(self, mock_cm_cls):
        mock_cm_cls.return_value = self._config(notify_on_error=True, email="ops@example.com")
        mock_server = MagicMock()
        with patch("utils.notifications.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = mock_server
            sent = notify_action_failure("delete_volume", "vol-123", "AccessDenied")

        self.assertTrue(sent)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("bot@x.com", "")
        mock_server.send_message.assert_called_once()
        sent_msg = mock_server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "ops@example.com")
        self.assertIn("delete_volume", sent_msg["Subject"])
        self.assertIn("vol-123", sent_msg["Subject"])

    @patch.dict("os.environ", {"SMTP_HOST": "smtp.example.com"}, clear=True)
    @patch("utils.notifications.ConfigManager")
    def test_smtp_failure_is_swallowed(self, mock_cm_cls):
        """A broken mail server must never raise back into the caller
        (an action-failure notification failing must not itself fail
        the action or the grace executor job)."""
        mock_cm_cls.return_value = self._config(notify_on_error=True, email="ops@example.com")
        with patch("utils.notifications.smtplib.SMTP", side_effect=OSError("connection refused")):
            sent = notify_action_failure("stop_instance", "i-123", "boom")
        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()
