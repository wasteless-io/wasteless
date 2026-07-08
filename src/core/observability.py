#!/usr/bin/env python3
"""
Optional Sentry error tracking for Wasteless.

Same degrade-silently contract as core/llm.py and ui/utils/notifications.py:
without SENTRY_DSN in the environment or without the sentry-sdk package
installed, init_sentry() is a no-op and wasteless runs exactly as before.
Nothing in the codebase may depend on Sentry being active.

Enable with:
    pip install sentry-sdk[fastapi]
    SENTRY_DSN=https://...@o0.ingest.sentry.io/0   # in .env

Called once at UI startup (ui/main.py). The detectors/collectors are
short-lived CLI runs whose failures already land in ~/.wasteless.log and
collection_runs — the long-running process worth instrumenting is the UI
with its APScheduler jobs (sync, grace executor, Terraform PR sync).
"""

import logging
import os

logger = logging.getLogger(__name__)

DSN_ENV_VAR = "SENTRY_DSN"


def init_sentry(component: str = "ui") -> bool:
    """Initialize Sentry if configured. Returns True when active.

    Never raises: a bad DSN or missing package logs one line and moves on.
    """
    dsn = os.getenv(DSN_ENV_VAR)
    if not dsn:
        return False

    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            # Errors only — no performance tracing, wasteless is not
            # latency-sensitive and traces multiply the event volume.
            traces_sample_rate=0.0,
            # Resource IDs appear in messages by design (they're the
            # subject of every action); nothing else user-identifying
            # is sent.
            send_default_pii=False,
        )
        sentry_sdk.set_tag("component", component)
        logger.info("Sentry error tracking enabled (component=%s)", component)
        return True
    except ImportError:
        logger.warning(
            "%s is set but sentry-sdk is not installed — "
            "pip install 'sentry-sdk[fastapi]' to enable error tracking",
            DSN_ENV_VAR,
        )
        return False
    except Exception as e:
        logger.warning("Sentry initialization failed (continuing without): %s", e)
        return False
