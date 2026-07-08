"""
UI-side access to the backend AWS client factory.

Imports the backend AWS client factory (src/core/aws_clients.py), which is
pip-installed editable into ui/venv (see pyproject.toml), so the UI and the
backend share one AssumeRole implementation (read role by default, write
role for remediation).

If the backend is not importable (UI deployed standalone without the
editable backend), falls back to plain boto3 clients on the default
credential chain — the pre-AssumeRole behavior.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

try:
    from core.aws_clients import get_client, reset_cache  # noqa: F401
    from core.config import ConfigurationError  # noqa: F401
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
