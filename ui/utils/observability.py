"""
UI-side access to the backend Sentry init (src/core/observability.py).

The backend is pip-installed editable into ui/venv (see pyproject.toml), so
`core.*` imports directly. If it isn't importable (UI deployed standalone
without the editable backend), init_sentry degrades to a no-op — same
contract as the backend implementation.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from core.observability import init_sentry  # noqa: F401
except ImportError as exc:  # pragma: no cover - standalone UI deployment
    logger.warning(
        "Backend observability not importable (%s); Sentry disabled",
        exc,
    )

    def init_sentry(component: str = "ui") -> bool:  # type: ignore[no-redef]
        return False
