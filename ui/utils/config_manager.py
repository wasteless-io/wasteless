"""
Configuration Manager for Wasteless Backend
Manages reading and writing to config/remediation.yaml

Thread-safe file operations with file locking to prevent race conditions.
"""

import os
import yaml
from typing import Dict, Any, Optional
import logging
import threading
import fcntl
from contextlib import contextmanager
import time

logger = logging.getLogger(__name__)

# Thread lock for in-process synchronization
_config_lock = threading.Lock()

# Lock timeout in seconds
_LOCK_TIMEOUT = 10.0


@contextmanager
def _file_lock(filepath: str, mode: str = 'r', exclusive: bool = False):
    """
    Context manager for file operations with file locking.

    Uses fcntl for cross-process file locking (Unix/macOS).
    Combined with threading.Lock for in-process thread safety.

    Args:
        filepath: Path to the file
        mode: File open mode ('r', 'w', 'r+', etc.)
        exclusive: If True, use exclusive lock (LOCK_EX), else shared lock (LOCK_SH)

    Yields:
        File object with lock acquired

    Raises:
        TimeoutError: If lock cannot be acquired within timeout
        IOError: If file operations fail
    """
    lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH

    # First, acquire thread lock for in-process synchronization
    acquired = _config_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        raise TimeoutError(f"Could not acquire thread lock within {_LOCK_TIMEOUT}s")

    file_obj = None
    try:
        # Open file
        file_obj = open(filepath, mode)

        # Try to acquire file lock with timeout using non-blocking approach
        start_time = time.time()
        while True:
            try:
                fcntl.flock(file_obj.fileno(), lock_type | fcntl.LOCK_NB)
                break  # Lock acquired successfully
            except BlockingIOError:
                if time.time() - start_time > _LOCK_TIMEOUT:
                    raise TimeoutError(
                        f"Could not acquire file lock on {filepath} within {_LOCK_TIMEOUT}s"
                    )
                time.sleep(0.1)  # Wait before retrying

        yield file_obj

    finally:
        # Release file lock if file was opened
        if file_obj is not None:
            try:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass  # Ignore unlock errors
            try:
                file_obj.close()
            except Exception:
                pass  # Ignore close errors

        # Release thread lock
        _config_lock.release()

# Path to backend config file
# ui/utils/ -> go up 2 levels -> project root (wasteless/)
BACKEND_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    '..', '..'
))
CONFIG_PATH = os.path.join(BACKEND_PATH, 'config', 'remediation.yaml')


# =============================================================================
# INPUT VALIDATION CONSTANTS AND UTILITIES
# =============================================================================

# Validation limits for configuration values
CONFIG_LIMITS = {
    'dry_run_days': {'min': 0, 'max': 30, 'type': int},
    'min_instance_age_days': {'min': 1, 'max': 365, 'type': int},
    'min_idle_days': {'min': 1, 'max': 90, 'type': int},
    'min_confidence_score': {'min': 0.0, 'max': 1.0, 'type': float},
    'max_instances_per_run': {'min': 1, 'max': 100, 'type': int},
}


class ConfigValidationError(ValueError):
    """Raised when a configuration value fails validation."""
    pass


def validate_config_value(key: str, value: Any) -> Any:
    """
    Validate a configuration value against defined limits.

    Args:
        key: Configuration key name
        value: Value to validate

    Returns:
        Validated and type-converted value

    Raises:
        ConfigValidationError: If validation fails
    """
    if key not in CONFIG_LIMITS:
        return value  # No validation rules defined, accept as-is

    limits = CONFIG_LIMITS[key]
    expected_type = limits['type']
    min_val = limits['min']
    max_val = limits['max']

    # Type conversion
    try:
        if expected_type == int:
            value = int(value)
        elif expected_type == float:
            value = float(value)
    except (ValueError, TypeError) as e:
        raise ConfigValidationError(
            f"Invalid type for {key}: expected {expected_type.__name__}, got {type(value).__name__}"
        ) from e

    # Range validation
    if value < min_val or value > max_val:
        raise ConfigValidationError(
            f"Value for {key} must be between {min_val} and {max_val}, got {value}"
        )

    return value


def validate_instance_id(instance_id: str) -> str:
    """
    Validate an EC2 instance ID format.

    Args:
        instance_id: Instance ID to validate

    Returns:
        Validated instance ID

    Raises:
        ConfigValidationError: If format is invalid
    """
    if not instance_id:
        raise ConfigValidationError("Instance ID cannot be empty")

    instance_id = str(instance_id).strip()

    # Basic format check: i-xxxxxxxxxxxxxxxxx (17-19 chars total)
    if not instance_id.startswith('i-'):
        raise ConfigValidationError(
            f"Invalid instance ID format: must start with 'i-', got '{instance_id}'"
        )

    # Check length (i- plus 8 or 17 hex chars)
    id_part = instance_id[2:]
    if len(id_part) not in (8, 17):
        raise ConfigValidationError(
            f"Invalid instance ID length: expected 10 or 19 characters, got {len(instance_id)}"
        )

    # Check hex characters
    if not all(c in '0123456789abcdef' for c in id_part.lower()):
        raise ConfigValidationError(
            f"Invalid instance ID format: must be hexadecimal after 'i-'"
        )

    return instance_id


class ConfigManager:
    """Manages backend configuration file"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config manager.

        Args:
            config_path: Optional path to config file. Uses default if not provided.
        """
        self.config_path = config_path or CONFIG_PATH
        self._config_cache = None

    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.

        Thread-safe with file locking to prevent race conditions.

        Returns:
            Dict containing configuration

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If YAML is invalid
            TimeoutError: If file lock cannot be acquired
        """
        try:
            with _file_lock(self.config_path, mode='r', exclusive=False) as f:
                self._config_cache = yaml.safe_load(f)
            logger.info(f"✅ Configuration loaded from {self.config_path}")
            return self._config_cache
        except FileNotFoundError:
            logger.error(f"❌ Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"❌ Invalid YAML in config file: {e}")
            raise
        except TimeoutError as e:
            logger.error(f"❌ Could not acquire lock for reading config: {e}")
            raise

    def save_config(self, config: Dict[str, Any]) -> bool:
        """
        Save configuration to YAML file.

        Thread-safe with file locking to prevent race conditions.
        Creates a backup before writing.

        Args:
            config: Configuration dictionary to save

        Returns:
            True if successful, False otherwise
        """
        backup_path = f"{self.config_path}.backup"

        try:
            # Use exclusive lock for the entire read-backup-write operation
            with _file_lock(self.config_path, mode='r+', exclusive=True) as f:
                # Read current content for backup
                current_content = f.read()

                # Write backup (outside the main file lock)
                try:
                    with open(backup_path, 'w') as backup_file:
                        backup_file.write(current_content)
                except Exception as backup_err:
                    logger.warning(f"⚠️ Could not create backup: {backup_err}")

                # Truncate and write new config
                f.seek(0)
                f.truncate()
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

            self._config_cache = config
            logger.info(f"✅ Configuration saved to {self.config_path}")
            return True

        except TimeoutError as e:
            logger.error(f"❌ Could not acquire lock for saving config: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to save config: {e}")
            # Try to restore from backup if it exists
            try:
                if os.path.exists(backup_path):
                    with open(backup_path, 'r') as src:
                        with open(self.config_path, 'w') as dst:
                            dst.write(src.read())
                    logger.info("⚠️ Config restored from backup")
            except Exception as restore_err:
                logger.error(f"❌ Failed to restore from backup: {restore_err}")
            return False

    def get_auto_remediation_enabled(self) -> bool:
        """Check if auto-remediation is enabled"""
        config = self.load_config()
        return config.get('auto_remediation', {}).get('enabled', False)

    def set_auto_remediation_enabled(self, enabled: bool) -> bool:
        """
        Enable or disable auto-remediation.

        Args:
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        config = self.load_config()
        if 'auto_remediation' not in config:
            config['auto_remediation'] = {}
        config['auto_remediation']['enabled'] = enabled
        return self.save_config(config)

    def get_dry_run(self) -> bool:
        """Check if dry-run mode is enabled (default: True for safety)"""
        config = self.load_config()
        return config.get('dry_run', True)

    def set_dry_run(self, enabled: bool) -> bool:
        """
        Enable or disable dry-run mode.

        Args:
            enabled: True for dry-run (safe), False for production mode

        Returns:
            True if successful
        """
        config = self.load_config()
        config['dry_run'] = bool(enabled)
        return self.save_config(config)

    def get_dry_run_days(self) -> int:
        """Get the mandatory dry-run period in days"""
        config = self.load_config()
        return config.get('auto_remediation', {}).get('dry_run_days', 7)

    def set_dry_run_days(self, days: int) -> bool:
        """
        Set the mandatory dry-run period.

        Args:
            days: Number of days (0 to disable dry-run requirement)

        Returns:
            True if successful

        Raises:
            ConfigValidationError: If days is out of valid range
        """
        # Validate input
        days = validate_config_value('dry_run_days', days)

        config = self.load_config()
        if 'auto_remediation' not in config:
            config['auto_remediation'] = {}
        config['auto_remediation']['dry_run_days'] = days
        return self.save_config(config)

    def get_protection_rules(self) -> Dict[str, Any]:
        """Get all protection rules"""
        config = self.load_config()
        return config.get('protection', {})

    def update_protection_rule(self, key: str, value: Any) -> bool:
        """
        Update a specific protection rule with validation.

        Args:
            key: Rule key (e.g., 'min_confidence_score')
            value: New value

        Returns:
            True if successful

        Raises:
            ConfigValidationError: If value fails validation
        """
        # Validate the value if rules exist for this key
        value = validate_config_value(key, value)

        config = self.load_config()
        if 'protection' not in config:
            config['protection'] = {}
        config['protection'][key] = value
        return self.save_config(config)

    def get_whitelist(self) -> Dict[str, Any]:
        """Get whitelist configuration"""
        config = self.load_config()
        return config.get('whitelist', {})

    def add_instance_to_whitelist(self, instance_id: str) -> bool:
        """
        Add an instance to the whitelist.

        Args:
            instance_id: EC2 instance ID

        Returns:
            True if successful

        Raises:
            ConfigValidationError: If instance_id format is invalid
        """
        # Validate instance ID format
        instance_id = validate_instance_id(instance_id)

        config = self.load_config()
        if 'whitelist' not in config:
            config['whitelist'] = {'instance_ids': []}
        if 'instance_ids' not in config['whitelist']:
            config['whitelist']['instance_ids'] = []

        if instance_id not in config['whitelist']['instance_ids']:
            config['whitelist']['instance_ids'].append(instance_id)
            return self.save_config(config)
        return True  # Already whitelisted

    def remove_instance_from_whitelist(self, instance_id: str) -> bool:
        """
        Remove an instance from the whitelist.

        Args:
            instance_id: EC2 instance ID

        Returns:
            True if successful
        """
        config = self.load_config()
        if 'whitelist' in config and 'instance_ids' in config['whitelist']:
            if instance_id in config['whitelist']['instance_ids']:
                config['whitelist']['instance_ids'].remove(instance_id)
                return self.save_config(config)
        return True  # Not in whitelist

    def get_schedule(self) -> Dict[str, Any]:
        """Get schedule configuration"""
        config = self.load_config()
        return config.get('schedule', {})

    def is_schedule_enabled(self) -> bool:
        """
        Check if schedule restrictions are active.
        Returns False if schedule allows all days/hours (no restrictions).
        """
        schedule = self.get_schedule()
        allowed_days = schedule.get('allowed_days', [])
        allowed_hours = schedule.get('allowed_hours', [])

        # If no restrictions or empty lists, schedule is disabled (allows all)
        return bool(allowed_days) and bool(allowed_hours)

    def disable_schedule_restrictions(self) -> bool:
        """
        Disable schedule restrictions by clearing allowed_days and allowed_hours.
        This allows execution at any time.

        Returns:
            True if successful
        """
        config = self.load_config()
        if 'schedule' not in config:
            config['schedule'] = {}

        # Empty lists = no restrictions
        config['schedule']['allowed_days'] = []
        config['schedule']['allowed_hours'] = []

        return self.save_config(config)

    def enable_schedule_restrictions(self, days: list = None, hours: list = None) -> bool:
        """
        Enable schedule restrictions with specified days and hours.

        Args:
            days: List of allowed days (e.g., ["Saturday", "Sunday"])
            hours: List of allowed hours (e.g., [2, 3, 4])

        Returns:
            True if successful
        """
        config = self.load_config()
        if 'schedule' not in config:
            config['schedule'] = {}

        if days is not None:
            config['schedule']['allowed_days'] = days
        if hours is not None:
            config['schedule']['allowed_hours'] = hours

        return self.save_config(config)

    def is_config_file_accessible(self) -> bool:
        """Check if config file exists and is accessible"""
        return os.path.exists(self.config_path) and os.access(self.config_path, os.R_OK | os.W_OK)


def get_backend_config_path() -> str:
    """Get the path to the backend config file"""
    return CONFIG_PATH
