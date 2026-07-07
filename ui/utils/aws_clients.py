"""
UI-side access to the backend AWS client factory.

Imports src/core/aws_clients.py through the same sys.path injection used
by ui/utils/remediator.py, so the UI and the backend share one AssumeRole
implementation (read role by default, write role for remediation).

If the backend is not importable (UI deployed standalone), falls back to
plain boto3 clients on the default credential chain — the pre-AssumeRole
behavior.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

BACKEND_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

try:
    from src.core.aws_clients import get_client, reset_cache  # noqa: F401
    from src.core.config import ConfigurationError  # noqa: F401
except ImportError as exc:  # pragma: no cover - standalone UI deployment
    logger.warning(
        "Backend aws_clients not importable (%s); falling back to plain "
        "boto3 clients on the default credential chain",
        exc,
    )

    class ConfigurationError(Exception):
        pass

    def reset_cache():
        pass

    def get_client(service, *, region=None, write=False, session_factory=None, **client_kwargs):
        import boto3

        return boto3.client(service, region_name=region, **client_kwargs)
