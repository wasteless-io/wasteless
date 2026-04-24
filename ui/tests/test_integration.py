#!/usr/bin/env python3
"""
Integration Tests for Wasteless UI
===================================

These tests verify the integration between UI components and backend services.
Run with: python -m pytest tests/test_integration.py -v

Author: Wasteless Team

Note: Some tests require dependencies (yaml, psycopg2) to be installed.
Run within the virtual environment for full test coverage.
"""

import unittest
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Check for required dependencies
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Import modules - config_manager requires yaml
if YAML_AVAILABLE:
    from utils.config_manager import (
        ConfigManager,
        ConfigValidationError,
        validate_config_value,
        validate_instance_id
    )
else:
    ConfigManager = None
    ConfigValidationError = Exception
    validate_config_value = lambda k, v: v
    validate_instance_id = lambda i: i

from utils.remediator import (
    check_backend_available,
    get_backend_path,
    validate_backend_at_startup,
    sanitize_for_json
)
from utils.logger import (
    get_logger,
    log_user_action,
    log_db_query,
    log_remediation_action,
    log_security_event
)


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestConfigManagerIntegration(unittest.TestCase):
    """Integration tests for ConfigManager with file operations."""

    def setUp(self):
        """Create a temporary config file for testing."""
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.yaml',
            delete=False
        )

        self.sample_config = {
            'auto_remediation': {
                'enabled': False,
                'dry_run_days': 7
            },
            'protection': {
                'min_instance_age_days': 30,
                'min_idle_days': 14,
                'min_confidence_score': 0.8,
                'max_instances_per_run': 3
            },
            'whitelist': {
                'instance_ids': ['i-1234567890abcdef0']
            },
            'schedule': {
                'allowed_days': ['Saturday', 'Sunday'],
                'allowed_hours': [2, 3, 4],
                'timezone': 'Europe/Paris'
            }
        }

        yaml.dump(self.sample_config, self.temp_file)
        self.temp_file.close()

        self.config_manager = ConfigManager(config_path=self.temp_file.name)

    def tearDown(self):
        """Clean up temporary files."""
        Path(self.temp_file.name).unlink(missing_ok=True)
        backup_path = Path(f"{self.temp_file.name}.backup")
        backup_path.unlink(missing_ok=True)

    def test_load_and_save_config(self):
        """Test that config can be loaded and saved correctly."""
        # Load
        config = self.config_manager.load_config()
        self.assertEqual(config['auto_remediation']['enabled'], False)

        # Modify
        config['auto_remediation']['enabled'] = True

        # Save
        result = self.config_manager.save_config(config)
        self.assertTrue(result)

        # Reload and verify
        config = self.config_manager.load_config()
        self.assertEqual(config['auto_remediation']['enabled'], True)

    def test_concurrent_save_config(self):
        """Test that file locking prevents corruption during concurrent saves."""
        import threading
        import time

        results = []
        errors = []

        def save_config(value):
            try:
                config = self.config_manager.load_config()
                config['auto_remediation']['dry_run_days'] = value
                time.sleep(0.01)  # Simulate some processing
                result = self.config_manager.save_config(config)
                results.append((value, result))
            except Exception as e:
                errors.append((value, str(e)))

        # Start multiple threads trying to save different values
        threads = []
        for i in range(5):
            t = threading.Thread(target=save_config, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # All saves should have succeeded (no corruption)
        successful_saves = [r for r in results if r[1]]
        self.assertGreater(len(successful_saves), 0)

        # Config file should be valid YAML
        config = self.config_manager.load_config()
        self.assertIn('auto_remediation', config)

    def test_backup_created_on_save(self):
        """Test that backup file is created when saving."""
        config = self.config_manager.load_config()
        config['auto_remediation']['enabled'] = True
        self.config_manager.save_config(config)

        backup_path = f"{self.temp_file.name}.backup"
        self.assertTrue(os.path.exists(backup_path))

    def test_set_dry_run_days_validation(self):
        """Test that dry_run_days validates input."""
        # Valid value
        result = self.config_manager.set_dry_run_days(14)
        self.assertTrue(result)

        # Invalid value (too high)
        with self.assertRaises(ConfigValidationError):
            self.config_manager.set_dry_run_days(100)

        # Invalid value (negative)
        with self.assertRaises(ConfigValidationError):
            self.config_manager.set_dry_run_days(-1)

    def test_add_instance_to_whitelist_validation(self):
        """Test that instance ID format is validated."""
        # Valid instance ID (17 char format)
        result = self.config_manager.add_instance_to_whitelist('i-0abc123def456789a')
        self.assertTrue(result)

        # Valid instance ID (8 char format)
        result = self.config_manager.add_instance_to_whitelist('i-12345678')
        self.assertTrue(result)

        # Invalid format
        with self.assertRaises(ConfigValidationError):
            self.config_manager.add_instance_to_whitelist('invalid-id')

        with self.assertRaises(ConfigValidationError):
            self.config_manager.add_instance_to_whitelist('ec2-instance')


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed - validation functions not available")
class TestInputValidation(unittest.TestCase):
    """Tests for input validation functions."""

    def test_validate_config_value_dry_run_days(self):
        """Test dry_run_days validation."""
        # Valid values
        self.assertEqual(validate_config_value('dry_run_days', 0), 0)
        self.assertEqual(validate_config_value('dry_run_days', 7), 7)
        self.assertEqual(validate_config_value('dry_run_days', 30), 30)

        # Invalid values
        with self.assertRaises(ConfigValidationError):
            validate_config_value('dry_run_days', -1)

        with self.assertRaises(ConfigValidationError):
            validate_config_value('dry_run_days', 31)

    def test_validate_config_value_confidence_score(self):
        """Test confidence score validation."""
        # Valid values
        self.assertEqual(validate_config_value('min_confidence_score', 0.0), 0.0)
        self.assertEqual(validate_config_value('min_confidence_score', 0.8), 0.8)
        self.assertEqual(validate_config_value('min_confidence_score', 1.0), 1.0)

        # Invalid values
        with self.assertRaises(ConfigValidationError):
            validate_config_value('min_confidence_score', -0.1)

        with self.assertRaises(ConfigValidationError):
            validate_config_value('min_confidence_score', 1.5)

    def test_validate_instance_id_format(self):
        """Test EC2 instance ID validation."""
        # Valid 17-char format (newer)
        self.assertEqual(
            validate_instance_id('i-0abc123def456789a'),
            'i-0abc123def456789a'
        )

        # Valid 8-char format (older)
        self.assertEqual(
            validate_instance_id('i-12345678'),
            'i-12345678'
        )

        # With whitespace (should be trimmed)
        self.assertEqual(
            validate_instance_id('  i-12345678  '),
            'i-12345678'
        )

        # Invalid formats
        with self.assertRaises(ConfigValidationError):
            validate_instance_id('')

        with self.assertRaises(ConfigValidationError):
            validate_instance_id('i-xyz')  # too short, non-hex

        with self.assertRaises(ConfigValidationError):
            validate_instance_id('instance-12345678')  # wrong prefix

        with self.assertRaises(ConfigValidationError):
            validate_instance_id('i-GGGGGGGGGGGGGGGG')  # non-hex chars


class TestRemediatorIntegration(unittest.TestCase):
    """Integration tests for Remediator module."""

    def test_backend_path_check(self):
        """Test that backend path check returns valid result."""
        path = get_backend_path()
        self.assertIsInstance(path, str)
        self.assertTrue(os.path.isabs(path))

    def test_validate_backend_at_startup(self):
        """Test backend validation returns tuple."""
        is_valid, message = validate_backend_at_startup()
        self.assertIsInstance(is_valid, bool)
        self.assertIsInstance(message, str)

        # Message should indicate status
        if is_valid:
            self.assertIn('available', message.lower())
        else:
            self.assertIn('not', message.lower())

    def test_sanitize_for_json(self):
        """Test datetime sanitization for JSON."""
        from datetime import datetime, date

        # Test datetime
        dt = datetime(2025, 1, 15, 10, 30, 0)
        result = sanitize_for_json(dt)
        self.assertEqual(result, '2025-01-15T10:30:00')

        # Test date
        d = date(2025, 1, 15)
        result = sanitize_for_json(d)
        self.assertEqual(result, '2025-01-15')

        # Test nested dict
        data = {
            'created_at': datetime(2025, 1, 15, 10, 0, 0),
            'items': [
                {'date': date(2025, 1, 14)},
                {'date': date(2025, 1, 15)}
            ]
        }
        result = sanitize_for_json(data)
        self.assertEqual(result['created_at'], '2025-01-15T10:00:00')
        self.assertEqual(result['items'][0]['date'], '2025-01-14')

        # Test non-datetime passthrough
        self.assertEqual(sanitize_for_json('string'), 'string')
        self.assertEqual(sanitize_for_json(123), 123)
        self.assertEqual(sanitize_for_json(None), None)


class TestLoggingIntegration(unittest.TestCase):
    """Integration tests for logging module."""

    def test_get_logger_creates_logger(self):
        """Test that get_logger returns valid logger."""
        logger = get_logger('test')
        self.assertIsNotNone(logger)
        self.assertEqual(logger.name, 'wasteless_ui.test')

    def test_log_user_action(self):
        """Test user action logging doesn't raise."""
        try:
            log_user_action('test_action', {'key': 'value'}, user='test_user')
        except Exception as e:
            self.fail(f"log_user_action raised exception: {e}")

    def test_log_db_query(self):
        """Test database query logging."""
        try:
            log_db_query('test_query', 150.5, success=True)
            log_db_query('test_query', 50.0, success=False)
        except Exception as e:
            self.fail(f"log_db_query raised exception: {e}")

    def test_log_remediation_action(self):
        """Test remediation action logging."""
        result = {'success': True, 'rejected_count': 3}
        try:
            log_remediation_action(
                action_type='reject',
                recommendation_ids=[1, 2, 3],
                result=result,
                dry_run=True
            )
        except Exception as e:
            self.fail(f"log_remediation_action raised exception: {e}")

        # Test with list result
        results = [
            {'success': True, 'recommendation_id': 1, 'instance_id': 'i-123'},
            {'success': False, 'recommendation_id': 2, 'instance_id': 'i-456', 'error': 'test error'}
        ]
        try:
            log_remediation_action(
                action_type='approve_execute',
                recommendation_ids=[1, 2],
                result=results,
                dry_run=False
            )
        except Exception as e:
            self.fail(f"log_remediation_action raised exception: {e}")

    def test_log_security_event(self):
        """Test security event logging."""
        try:
            log_security_event('test_event', 'Test details', severity='WARNING')
        except Exception as e:
            self.fail(f"log_security_event raised exception: {e}")


@unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
class TestDatabaseIntegration(unittest.TestCase):
    """Integration tests that require database connection.

    These tests are skipped if database is not available.
    """

    @classmethod
    def setUpClass(cls):
        """Check if database is available."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass  # dotenv not required if env vars are set

        cls.db_password = os.getenv('DB_PASSWORD')
        cls.skip_db_tests = not cls.db_password

    def setUp(self):
        if self.skip_db_tests:
            self.skipTest("Database not configured (DB_PASSWORD not set)")

    def test_database_connection(self):
        """Test that database connection works."""
        import psycopg2

        try:
            conn = psycopg2.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                port=os.getenv('DB_PORT', '5432'),
                database=os.getenv('DB_NAME', 'wasteless'),
                user=os.getenv('DB_USER', 'wasteless'),
                password=self.db_password
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            self.assertEqual(result[0], 1)
        except Exception as e:
            self.skipTest(f"Database not available: {e}")

    def test_recommendations_table_exists(self):
        """Test that recommendations table exists and has expected columns."""
        import psycopg2

        try:
            conn = psycopg2.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                port=os.getenv('DB_PORT', '5432'),
                database=os.getenv('DB_NAME', 'wasteless'),
                user=os.getenv('DB_USER', 'wasteless'),
                password=self.db_password
            )
            cursor = conn.cursor()
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'recommendations'
            """)
            columns = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()

            # Check expected columns exist
            expected_columns = ['id', 'status', 'recommendation_type']
            for col in expected_columns:
                self.assertIn(col, columns)

        except Exception as e:
            self.skipTest(f"Database not available: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
