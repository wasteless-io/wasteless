"""
UI-side access to the backend Sentry init (src/core/observability.py),
through the same sys.path injection as ui/utils/aws_clients.py.

If the backend is not importable (UI deployed standalone), init_sentry
degrades to a no-op — same contract as the backend implementation.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

BACKEND_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

try:
    from src.core.observability import init_sentry  # noqa: F401
except ImportError as exc:  # pragma: no cover - standalone UI deployment
    logger.warning(
        "Backend observability not importable (%s); Sentry disabled",
        exc,
    )

    def init_sentry(component: str = "ui") -> bool:  # type: ignore[no-redef]
        return False
