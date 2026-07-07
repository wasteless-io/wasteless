"""
Email notifications for Wasteless — sends a message when a remediation
action fails, if notifications.notify_on_error is enabled and an email
address is configured (config/remediation.yaml, editable from Settings).

V1 ships email only. notifications.slack_webhook is accepted and
preserved by policy export/import (see ui/utils/policies.py) for forward
compatibility, but nothing sends to it yet.

SMTP credentials come from the environment (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_USE_TLS) — never from the
versionable policy YAML. Silently no-ops (same pattern as
src/core/llm.py) when SMTP isn't configured or the send fails, so a
missing/broken mail server never blocks or fails the calling action.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


def _smtp_settings() -> Optional[dict]:
    host = os.getenv("SMTP_HOST")
    if not host:
        return None
    user = os.getenv("SMTP_USER", "")
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": user,
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_addr": os.getenv("SMTP_FROM") or user,
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() != "false",
    }


def notify_action_failure(action_type: str, resource_id: str, error: Optional[str]) -> bool:
    """Email the configured recipient when a remediation action fails.

    Returns True if a notification was actually sent, False if disabled,
    unconfigured, or the send failed — callers must never let this raise
    or block the action whose failure it's reporting.
    """
    try:
        notifications = ConfigManager().get_notifications()
        if not notifications.get("notify_on_error"):
            return False

        to_addr = (notifications.get("email") or "").strip()
        if not to_addr:
            return False

        smtp = _smtp_settings()
        if smtp is None:
            logger.debug(
                "notify_on_error is enabled but SMTP_HOST is not set — " "skipping failure email"
            )
            return False

        msg = EmailMessage()
        msg["Subject"] = f"[wasteless] Action failed: {action_type} on {resource_id}"
        msg["From"] = smtp["from_addr"]
        msg["To"] = to_addr
        msg.set_content(
            "Wasteless failed to execute a remediation action.\n\n"
            f"Action: {action_type}\n"
            f"Resource: {resource_id}\n"
            f"Error: {error or '(no details)'}\n"
        )

        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=10) as server:
            if smtp["use_tls"]:
                server.starttls()
            if smtp["user"]:
                server.login(smtp["user"], smtp["password"])
            server.send_message(msg)

        return True

    except Exception as e:
        logger.warning(f"Failed to send action-failure notification email: {e}")
        return False
