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
from typing import Any, Callable, Optional

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

    class ConfigurationError(Exception):  # type: ignore[no-redef]
        pass

    # Fallback signatures must stay identical to src/core/aws_clients.py —
    # mypy enforces this (conditional function variants).
    def reset_cache() -> None:  # type: ignore[no-redef]
        pass

    def get_client(  # type: ignore[no-redef]
        service: str,
        *,
        region: Optional[str] = None,
        write: bool = False,
        session_factory: Optional[Callable] = None,
        **client_kwargs: Any,
    ):
        import boto3

        return boto3.client(service, region_name=region, **client_kwargs)
