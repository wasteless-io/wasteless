#!/usr/bin/env python3
"""
Remediator Integration for Wasteless UI
========================================

This module provides integration with the backend EC2Remediator.
It handles execution of remediation actions from the UI.

Author: Wasteless Team
"""

import os
import sys
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime, date
import threading
from contextlib import contextmanager

# Add backend path to sys.path to import backend modules
# Path structure: wasteless-ui/utils/ -> go up 2 levels -> wasteless/
BACKEND_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    '..', '..',
    'wasteless'
))

# Thread lock for protecting remediator initialization
_remediator_lock = threading.Lock()

# Backend availability status (cached after first check)
_backend_available: Optional[bool] = None
_backend_check_error: Optional[str] = None


def _check_backend_path() -> tuple[bool, Optional[str]]:
    """
    Check if the backend path exists and contains required modules.

    Returns:
        Tuple of (is_available, error_message)
    """
    if not os.path.exists(BACKEND_PATH):
        return False, f"Backend directory not found: {BACKEND_PATH}"

    # Check for required backend structure
    src_path = os.path.join(BACKEND_PATH, 'src')
    if not os.path.exists(src_path):
        return False, f"Backend 'src' directory not found: {src_path}"

    remediator_path = os.path.join(src_path, 'remediators', 'ec2_remediator.py')
    if not os.path.exists(remediator_path):
        return False, f"EC2Remediator module not found: {remediator_path}"

    return True, None


# Initialize backend path check
_backend_exists, _backend_error = _check_backend_path()
if _backend_exists:
    if BACKEND_PATH not in sys.path:
        sys.path.insert(0, BACKEND_PATH)
else:
    logging.warning(f"Backend not available: {_backend_error}")

logger = logging.getLogger(__name__)


def sanitize_for_json(obj: Any) -> Any:
    """
    Convert datetime objects to ISO format strings for JSON serialization.

    Args:
        obj: Object that may contain datetime objects

    Returns:
        Sanitized object with datetimes converted to strings
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(sanitize_for_json(item) for item in obj)
    else:
        return obj


@contextmanager
def _backend_context():
    """
    Context manager to safely set up backend environment without changing cwd.

    Instead of os.chdir() (which is not thread-safe), we temporarily modify
    environment variables that the backend might use for config file paths.
    """
    original_env = os.environ.copy()
    try:
        # Set environment variable for backend config path instead of changing cwd
        os.environ['WASTELESS_CONFIG_DIR'] = os.path.join(BACKEND_PATH, 'config')
        os.environ['WASTELESS_BACKEND_PATH'] = BACKEND_PATH
        yield
    finally:
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)


class RemediatorProxy:
    """
    Proxy class to execute remediation actions via the backend engine.

    This provides a clean interface for the UI to trigger backend operations.
    Thread-safe initialization with proper error handling.
    """

    # Class-level cache for the remediator instance (per dry_run mode)
    _instances: Dict[bool, Any] = {}

    def __init__(self, dry_run: bool = True):
        """
        Initialize the remediator proxy.

        Args:
            dry_run: If True, no actual AWS actions are executed

        Raises:
            RuntimeError: If backend is not available
        """
        self.dry_run = dry_run
        self._remediator = None

        # Early validation of backend availability
        if not _backend_exists:
            raise RuntimeError(
                f"Backend not available: {_backend_error}\n"
                f"Please ensure wasteless backend is cloned at: {BACKEND_PATH}"
            )

    def _get_remediator(self):
        """
        Lazy load the EC2Remediator from backend.

        Thread-safe initialization using a lock to prevent race conditions.
        No os.chdir() is used - instead we set environment variables.
        """
        if self._remediator is not None:
            return self._remediator

        # Thread-safe initialization
        with _remediator_lock:
            # Double-check after acquiring lock
            if self._remediator is not None:
                return self._remediator

            # Check class-level cache first
            if self.dry_run in RemediatorProxy._instances:
                self._remediator = RemediatorProxy._instances[self.dry_run]
                return self._remediator

            try:
                with _backend_context():
                    # Import with timeout protection would require multiprocessing
                    # For now, we do a simple import with good error handling
                    from src.remediators.ec2_remediator import EC2Remediator

                    # Initialize remediator - pass config path explicitly if supported
                    config_path = os.path.join(BACKEND_PATH, 'config')
                    self._remediator = EC2Remediator(dry_run=self.dry_run)

                    # Cache at class level
                    RemediatorProxy._instances[self.dry_run] = self._remediator

                    logger.info(f"✅ EC2Remediator initialized (dry_run={self.dry_run})")

            except ImportError as e:
                logger.error(f"❌ Cannot import EC2Remediator: {e}")
                logger.error(f"   Backend path: {BACKEND_PATH}")
                logger.error(f"   Make sure wasteless backend is cloned next to wasteless-ui")
                raise RuntimeError(
                    f"Cannot import backend remediator: {e}\n"
                    f"Ensure wasteless backend is installed at: {BACKEND_PATH}"
                ) from e
            except Exception as e:
                logger.error(f"❌ Failed to initialize EC2Remediator: {e}")
                raise RuntimeError(f"Failed to initialize remediator: {e}") from e

        return self._remediator

    def execute_recommendations(
        self,
        conn,
        recommendation_ids: List[int]
    ) -> List[Dict]:
        """
        Execute multiple recommendations.

        Args:
            conn: PostgreSQL database connection
            recommendation_ids: List of recommendation IDs to execute

        Returns:
            List of execution results with status and details
        """
        results = []
        remediator = self._get_remediator()

        for rec_id in recommendation_ids:
            try:
                # Get instance_id and recommendation details from database
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        w.resource_id,
                        r.recommendation_type,
                        r.action_required,
                        w.confidence_score
                    FROM recommendations r
                    JOIN waste_detected w ON r.waste_id = w.id
                    WHERE r.id = %s
                """, (rec_id,))

                row = cursor.fetchone()
                cursor.close()

                if not row:
                    results.append({
                        'recommendation_id': rec_id,
                        'success': False,
                        'error': f'Recommendation {rec_id} not found in database',
                        'instance_id': None
                    })
                    continue

                instance_id, rec_type, action, confidence = row

                # Only execute stop or terminate actions
                if rec_type not in ['stop_instance', 'terminate_instance']:
                    results.append({
                        'recommendation_id': rec_id,
                        'instance_id': instance_id,
                        'success': False,
                        'error': f'Action type {rec_type} not supported yet (only stop/terminate)',
                        'recommendation_type': rec_type
                    })
                    continue

                # Execute the stop action (works for both stop and terminate intent)
                result = remediator.stop_instance(
                    instance_id=instance_id,
                    recommendation_id=rec_id,
                    reason=action
                )

                # Add recommendation_id to result
                result['recommendation_id'] = rec_id
                result['recommendation_type'] = rec_type
                result['confidence'] = float(confidence)

                # Sanitize datetime objects for JSON serialization
                result = sanitize_for_json(result)

                results.append(result)

            except Exception as e:
                logger.error(f"Error executing recommendation {rec_id}: {e}")
                results.append({
                    'recommendation_id': rec_id,
                    'success': False,
                    'error': str(e),
                    'instance_id': None
                })

        return results

    def reject_recommendations(
        self,
        conn,
        recommendation_ids: List[int],
        reason: str = "Rejected by user"
    ) -> Dict:
        """
        Reject recommendations (mark as rejected in database).

        Args:
            conn: PostgreSQL database connection
            recommendation_ids: List of recommendation IDs to reject
            reason: Optional reason for rejection

        Returns:
            Dict with count of rejected recommendations
        """
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recommendations
                SET status = 'rejected',
                    applied_at = NOW()
                WHERE id = ANY(%s)
                RETURNING id
            """, (recommendation_ids,))

            rejected_ids = [row[0] for row in cursor.fetchall()]
            conn.commit()
            cursor.close()

            return {
                'success': True,
                'rejected_count': len(rejected_ids),
                'rejected_ids': rejected_ids
            }
        except Exception as e:
            logger.error(f"Error rejecting recommendations: {e}")
            return {
                'success': False,
                'error': str(e),
                'rejected_count': 0
            }


def check_backend_available() -> bool:
    """
    Check if the backend is available and properly configured.

    Returns:
        True if backend can be imported, False otherwise
    """
    global _backend_available

    # Return cached result if available
    if _backend_available is not None:
        return _backend_available

    # First check if path exists
    if not _backend_exists:
        _backend_available = False
        return False

    # Try to import the module
    try:
        if BACKEND_PATH not in sys.path:
            sys.path.insert(0, BACKEND_PATH)
        from src.remediators.ec2_remediator import EC2Remediator
        _backend_available = True
        return True
    except ImportError as e:
        logger.warning(f"Backend import failed: {e}")
        _backend_available = False
        return False


def get_backend_path() -> str:
    """Get the expected backend path."""
    return BACKEND_PATH


def get_backend_error() -> Optional[str]:
    """Get the backend availability error message, if any."""
    if _backend_exists:
        return None
    return _backend_error


def validate_backend_at_startup() -> tuple[bool, str]:
    """
    Validate backend availability at application startup.

    Returns:
        Tuple of (is_valid, message) for display to user
    """
    if not _backend_exists:
        return False, (
            f"❌ Backend not found at: {BACKEND_PATH}\n\n"
            f"Error: {_backend_error}\n\n"
            f"To fix this, clone the wasteless backend:\n"
            f"```\n"
            f"cd {os.path.dirname(BACKEND_PATH)}\n"
            f"git clone https://github.com/wastelessio/wasteless.git\n"
            f"```"
        )

    if not check_backend_available():
        return False, (
            f"❌ Backend found but cannot import EC2Remediator\n\n"
            f"Backend path: {BACKEND_PATH}\n\n"
            f"Check that all dependencies are installed in the backend."
        )

    return True, "✅ Backend available and ready"
