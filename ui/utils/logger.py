"""
Structured logging configuration for Wasteless UI
"""
import logging
import sys
from datetime import datetime
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output."""

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname:8}{self.RESET}"
        return super().format(record)


def setup_logging(log_level=logging.INFO, log_file=None):
    """
    Setup structured logging for the application.

    Args:
        log_level: Logging level (default: INFO)
        log_file: Optional file path for logging. If None, uses default location.

    Returns:
        Logger instance
    """
    # Create logger
    logger = logging.getLogger('wasteless_ui')
    logger.setLevel(log_level)

    # Remove existing handlers
    logger.handlers = []

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_formatter = ColoredFormatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (if log_file specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name=None):
    """
    Get a logger instance.

    Args:
        name: Logger name (default: wasteless_ui)

    Returns:
        Logger instance
    """
    logger_name = f'wasteless_ui.{name}' if name else 'wasteless_ui'
    return logging.getLogger(logger_name)


# Initialize default logger
default_logger = setup_logging(
    log_level=logging.INFO,
    log_file='logs/wasteless_ui.log'
)


# Convenience functions
def log_user_action(action: str, details: dict = None, user: str = "streamlit_user"):
    """Log user actions for audit trail."""
    logger = get_logger('user_actions')
    details_str = f" | {details}" if details else ""
    logger.info(f"User: {user} | Action: {action}{details_str}")


def log_db_query(query_name: str, duration_ms: float, success: bool = True):
    """Log database query performance."""
    logger = get_logger('database')
    status = "SUCCESS" if success else "FAILED"
    logger.info(f"Query: {query_name} | Duration: {duration_ms:.2f}ms | Status: {status}")


def log_error(error: Exception, context: str = None):
    """Log errors with context."""
    logger = get_logger('errors')
    context_str = f" | Context: {context}" if context else ""
    logger.error(f"Error: {type(error).__name__}: {str(error)}{context_str}", exc_info=True)


def log_config_change(setting: str, old_value: any, new_value: any, user: str = "streamlit_user"):
    """Log configuration changes."""
    logger = get_logger('config')
    logger.warning(f"Config Change | User: {user} | Setting: {setting} | Old: {old_value} | New: {new_value}")


def log_cache_stats(cache_name: str, hits: int, misses: int):
    """Log cache statistics."""
    logger = get_logger('cache')
    hit_rate = (hits / (hits + misses) * 100) if (hits + misses) > 0 else 0
    logger.debug(f"Cache: {cache_name} | Hits: {hits} | Misses: {misses} | Hit Rate: {hit_rate:.1f}%")


def log_remediation_action(
    action_type: str,
    recommendation_ids: list,
    result: dict,
    dry_run: bool = True,
    user: str = "streamlit_user"
):
    """
    Log remediation actions (approve/reject) for audit trail.

    This is critical for compliance and debugging.

    Args:
        action_type: Type of action ('approve', 'reject', 'execute')
        recommendation_ids: List of recommendation IDs affected
        result: Result dict from the operation
        dry_run: Whether this was a dry-run
        user: User who performed the action
    """
    logger = get_logger('audit')

    # Determine success/failure - check for list FIRST before calling .get()
    if isinstance(result, list):
        # Multiple results - count successes
        success_count = len([r for r in result if r.get('success', False)])
        total_count = len(result)
        success = success_count > 0
        status = f"SUCCESS ({success_count}/{total_count})" if success else "FAILED"
    else:
        success = result.get('success', False)
        status = "SUCCESS" if success else "FAILED"

    # Build log message
    mode = "DRY-RUN" if dry_run else "PRODUCTION"
    ids_str = ','.join(map(str, recommendation_ids))

    # Log at WARNING level for production actions, INFO for dry-run
    log_level = logging.WARNING if not dry_run else logging.INFO

    message = (
        f"AUDIT | User: {user} | Action: {action_type.upper()} | "
        f"Mode: {mode} | IDs: [{ids_str}] | Status: {status}"
    )

    # Add error details if failed (only for dict results)
    if not success and isinstance(result, dict) and 'error' in result:
        message += f" | Error: {result['error']}"

    logger.log(log_level, message)

    # Also log individual results for detailed audit
    if isinstance(result, list):
        for r in result:
            instance_id = r.get('instance_id', 'unknown')
            rec_id = r.get('recommendation_id', 'unknown')
            rec_status = "SUCCESS" if r.get('success', False) else "FAILED"
            error = r.get('error', '')
            logger.info(
                f"  └─ Rec#{rec_id} | Instance: {instance_id} | {rec_status}"
                + (f" | Error: {error}" if error else "")
            )


def log_security_event(event_type: str, details: str, severity: str = "WARNING"):
    """
    Log security-related events.

    Args:
        event_type: Type of security event (e.g., 'insecure_password', 'invalid_input')
        details: Description of the event
        severity: Log severity ('INFO', 'WARNING', 'ERROR', 'CRITICAL')
    """
    logger = get_logger('security')
    level = getattr(logging, severity.upper(), logging.WARNING)
    logger.log(level, f"SECURITY | Event: {event_type} | {details}")
