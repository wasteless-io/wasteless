"""
Unit tests for src/core/observability.py — optional Sentry init.

The contract under test is degrade-silently: whatever is wrong (no DSN,
package missing, bad DSN), init_sentry() returns False and never raises.
"""

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.observability import DSN_ENV_VAR, init_sentry


class TestInitSentry:

    def test_no_dsn_is_a_noop(self, monkeypatch):
        monkeypatch.delenv(DSN_ENV_VAR, raising=False)
        assert init_sentry() is False

    def test_dsn_without_package_returns_false(self, monkeypatch):
        monkeypatch.setenv(DSN_ENV_VAR, "https://key@o0.ingest.sentry.io/0")
        with patch.dict(sys.modules, {"sentry_sdk": None}):
            assert init_sentry() is False

    def test_dsn_with_package_initializes(self, monkeypatch):
        monkeypatch.setenv(DSN_ENV_VAR, "https://key@o0.ingest.sentry.io/0")
        mock_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            assert init_sentry(component="ui") is True

        kwargs = mock_sdk.init.call_args.kwargs
        assert kwargs["dsn"] == "https://key@o0.ingest.sentry.io/0"
        assert kwargs["traces_sample_rate"] == 0.0  # errors only, no tracing
        assert kwargs["send_default_pii"] is False
        mock_sdk.set_tag.assert_called_once_with("component", "ui")

    def test_init_failure_returns_false(self, monkeypatch):
        """A malformed DSN (or any SDK error) must not break startup."""
        monkeypatch.setenv(DSN_ENV_VAR, "not-a-dsn")
        mock_sdk = MagicMock()
        mock_sdk.init.side_effect = RuntimeError("invalid DSN")
        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            assert init_sentry() is False
