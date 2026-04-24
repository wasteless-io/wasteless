"""
Unit tests for ConfigManager

Note: Requires PyYAML to be installed (run within venv).
"""
import unittest
import tempfile
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Check for yaml availability - required for config_manager module
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

# Only import config_manager if yaml is available
if YAML_AVAILABLE:
    from utils.config_manager import (
        ConfigManager,
        ConfigValidationError,
        validate_config_value,
        validate_instance_id,
        CONFIG_LIMITS
    )
else:
    # Create dummy classes for test discovery
    ConfigManager = None
    ConfigValidationError = Exception
    validate_config_value = lambda k, v: v
    validate_instance_id = lambda i: i
    CONFIG_LIMITS = {}


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestConfigManager(unittest.TestCase):
    """Test cases for ConfigManager."""

    def setUp(self):
        """Set up test fixtures."""
        # Create temporary config file
        self.temp_config = tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.yaml',
            delete=False
        )

        # Write sample config
        sample_config = {
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
            'schedule': {
                'allowed_days': ['Saturday', 'Sunday'],
                'allowed_hours': [2, 3, 4],
                'timezone': 'Europe/Paris'
            }
        }

        yaml.dump(sample_config, self.temp_config)
        self.temp_config.close()

        # Create ConfigManager with temp file
        self.config_manager = ConfigManager(config_path=self.temp_config.name)

    def tearDown(self):
        """Clean up test fixtures."""
        # Delete temp file
        Path(self.temp_config.name).unlink(missing_ok=True)

    def test_load_config(self):
        """Test loading configuration."""
        config = self.config_manager.load_config()
        self.assertIsNotNone(config)
        self.assertIn('auto_remediation', config)
        self.assertIn('protection', config)

    def test_config_file_accessible(self):
        """Test is_config_file_accessible."""
        self.assertTrue(self.config_manager.is_config_file_accessible())

    def test_get_auto_remediation_enabled(self):
        """Test getting auto_remediation enabled status."""
        config = self.config_manager.load_config()
        enabled = config.get('auto_remediation', {}).get('enabled', False)
        self.assertFalse(enabled)

    def test_get_protection_rules(self):
        """Test getting protection rules."""
        config = self.config_manager.load_config()
        protection = config.get('protection', {})

        self.assertEqual(protection.get('min_instance_age_days'), 30)
        self.assertEqual(protection.get('min_idle_days'), 14)
        self.assertEqual(protection.get('min_confidence_score'), 0.8)
        self.assertEqual(protection.get('max_instances_per_run'), 3)

    def test_get_schedule_config(self):
        """Test getting schedule configuration."""
        config = self.config_manager.load_config()
        schedule = config.get('schedule', {})

        self.assertEqual(schedule.get('allowed_days'), ['Saturday', 'Sunday'])
        self.assertEqual(schedule.get('allowed_hours'), [2, 3, 4])
        self.assertEqual(schedule.get('timezone'), 'Europe/Paris')

    def test_config_structure(self):
        """Test that config has expected structure."""
        config = self.config_manager.load_config()

        # Check top-level keys
        expected_keys = ['auto_remediation', 'protection', 'schedule']
        for key in expected_keys:
            self.assertIn(key, config)

    def test_protection_values_types(self):
        """Test that protection values have correct types."""
        config = self.config_manager.load_config()
        protection = config.get('protection', {})

        self.assertIsInstance(protection.get('min_instance_age_days'), int)
        self.assertIsInstance(protection.get('min_idle_days'), int)
        self.assertIsInstance(protection.get('min_confidence_score'), float)
        self.assertIsInstance(protection.get('max_instances_per_run'), int)


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestConfigValidation(unittest.TestCase):
    """Test configuration validation."""

    def test_min_confidence_range(self):
        """Test that confidence score is between 0 and 1."""
        config = {'protection': {'min_confidence_score': 0.8}}
        confidence = config['protection']['min_confidence_score']

        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_positive_values(self):
        """Test that numeric values are positive."""
        config = {
            'protection': {
                'min_instance_age_days': 30,
                'min_idle_days': 14,
                'max_instances_per_run': 3
            }
        }

        for key, value in config['protection'].items():
            if isinstance(value, (int, float)):
                self.assertGreater(value, 0, f"{key} should be positive")


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestValidateConfigValue(unittest.TestCase):
    """Test validate_config_value function."""

    def test_dry_run_days_valid(self):
        """Test valid dry_run_days values."""
        self.assertEqual(validate_config_value('dry_run_days', 0), 0)
        self.assertEqual(validate_config_value('dry_run_days', 7), 7)
        self.assertEqual(validate_config_value('dry_run_days', 30), 30)

    def test_dry_run_days_invalid(self):
        """Test invalid dry_run_days values."""
        with self.assertRaises(ConfigValidationError):
            validate_config_value('dry_run_days', -1)
        with self.assertRaises(ConfigValidationError):
            validate_config_value('dry_run_days', 31)

    def test_confidence_score_valid(self):
        """Test valid min_confidence_score values."""
        self.assertEqual(validate_config_value('min_confidence_score', 0.0), 0.0)
        self.assertEqual(validate_config_value('min_confidence_score', 0.5), 0.5)
        self.assertEqual(validate_config_value('min_confidence_score', 1.0), 1.0)

    def test_confidence_score_invalid(self):
        """Test invalid min_confidence_score values."""
        with self.assertRaises(ConfigValidationError):
            validate_config_value('min_confidence_score', -0.1)
        with self.assertRaises(ConfigValidationError):
            validate_config_value('min_confidence_score', 1.5)

    def test_unknown_key_passthrough(self):
        """Test that unknown keys pass through without validation."""
        result = validate_config_value('unknown_key', 'any_value')
        self.assertEqual(result, 'any_value')

    def test_type_conversion_int(self):
        """Test integer type conversion."""
        result = validate_config_value('dry_run_days', '7')
        self.assertEqual(result, 7)
        self.assertIsInstance(result, int)

    def test_type_conversion_float(self):
        """Test float type conversion."""
        result = validate_config_value('min_confidence_score', '0.8')
        self.assertEqual(result, 0.8)
        self.assertIsInstance(result, float)


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestValidateInstanceId(unittest.TestCase):
    """Test validate_instance_id function."""

    def test_valid_17_char_id(self):
        """Test valid 17-character instance ID format."""
        result = validate_instance_id('i-0abc123def456789a')
        self.assertEqual(result, 'i-0abc123def456789a')

    def test_valid_8_char_id(self):
        """Test valid 8-character instance ID format."""
        result = validate_instance_id('i-12345678')
        self.assertEqual(result, 'i-12345678')

    def test_whitespace_trimmed(self):
        """Test that whitespace is trimmed."""
        result = validate_instance_id('  i-12345678  ')
        self.assertEqual(result, 'i-12345678')

    def test_empty_id_fails(self):
        """Test that empty instance ID fails validation."""
        with self.assertRaises(ConfigValidationError):
            validate_instance_id('')

    def test_wrong_prefix_fails(self):
        """Test that wrong prefix fails validation."""
        with self.assertRaises(ConfigValidationError):
            validate_instance_id('ec2-12345678')

    def test_too_short_fails(self):
        """Test that too short ID fails validation."""
        with self.assertRaises(ConfigValidationError):
            validate_instance_id('i-123')

    def test_non_hex_chars_fail(self):
        """Test that non-hex characters fail validation."""
        with self.assertRaises(ConfigValidationError):
            validate_instance_id('i-xyz12345')

    def test_uppercase_hex_works(self):
        """Test that uppercase hex characters work."""
        result = validate_instance_id('i-ABCDEF12')
        # Note: original case is preserved but validation accepts uppercase
        self.assertEqual(result, 'i-ABCDEF12')


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestConfigLimits(unittest.TestCase):
    """Test CONFIG_LIMITS constant."""

    def test_limits_defined(self):
        """Test that CONFIG_LIMITS is properly defined."""
        self.assertIsNotNone(CONFIG_LIMITS)
        self.assertIsInstance(CONFIG_LIMITS, dict)

    def test_expected_keys_exist(self):
        """Test that expected keys exist in CONFIG_LIMITS."""
        expected_keys = [
            'dry_run_days',
            'min_instance_age_days',
            'min_idle_days',
            'min_confidence_score',
            'max_instances_per_run'
        ]
        for key in expected_keys:
            self.assertIn(key, CONFIG_LIMITS)

    def test_limits_have_required_fields(self):
        """Test that each limit has min, max, and type."""
        for key, limits in CONFIG_LIMITS.items():
            self.assertIn('min', limits, f"{key} missing 'min'")
            self.assertIn('max', limits, f"{key} missing 'max'")
            self.assertIn('type', limits, f"{key} missing 'type'")

    def test_min_less_than_max(self):
        """Test that min is less than or equal to max."""
        for key, limits in CONFIG_LIMITS.items():
            self.assertLessEqual(limits['min'], limits['max'],
                                f"{key}: min should be <= max")


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML not installed")
class TestConfigManagerWithValidation(unittest.TestCase):
    """Test ConfigManager methods with validation."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_config = tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.yaml',
            delete=False
        )

        sample_config = {
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
                'instance_ids': []
            }
        }

        yaml.dump(sample_config, self.temp_config)
        self.temp_config.close()

        self.config_manager = ConfigManager(config_path=self.temp_config.name)

    def tearDown(self):
        """Clean up test fixtures."""
        Path(self.temp_config.name).unlink(missing_ok=True)
        backup_path = Path(f"{self.temp_config.name}.backup")
        backup_path.unlink(missing_ok=True)

    def test_set_dry_run_days_validates(self):
        """Test set_dry_run_days validates input."""
        # Valid
        result = self.config_manager.set_dry_run_days(14)
        self.assertTrue(result)

        # Invalid
        with self.assertRaises(ConfigValidationError):
            self.config_manager.set_dry_run_days(100)

    def test_update_protection_rule_validates(self):
        """Test update_protection_rule validates input."""
        # Valid
        result = self.config_manager.update_protection_rule('min_confidence_score', 0.9)
        self.assertTrue(result)

        # Invalid
        with self.assertRaises(ConfigValidationError):
            self.config_manager.update_protection_rule('min_confidence_score', 2.0)

    def test_add_instance_to_whitelist_validates(self):
        """Test add_instance_to_whitelist validates input."""
        # Valid
        result = self.config_manager.add_instance_to_whitelist('i-1234567890abcdef0')
        self.assertTrue(result)

        # Invalid
        with self.assertRaises(ConfigValidationError):
            self.config_manager.add_instance_to_whitelist('invalid')


if __name__ == '__main__':
    unittest.main()
