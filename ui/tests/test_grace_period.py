#!/usr/bin/env python3
"""
Unit tests for the approval grace period — ConfigManager accessors and
validation limits.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from utils.config_manager import (
    ConfigManager,
    ConfigValidationError,
    validate_config_value,
)


class TestGracePeriodConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.safe_dump({"auto_remediation": {"enabled": False}}, self.tmp)
        self.tmp.close()
        self.manager = ConfigManager(config_path=self.tmp.name)

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_defaults_to_zero_when_absent(self):
        self.assertEqual(self.manager.get_grace_period_days(), 0)

    def test_set_and_get(self):
        self.assertTrue(self.manager.set_grace_period_days(3))
        # Fresh manager: value must have been persisted to the file
        fresh = ConfigManager(config_path=self.tmp.name)
        self.assertEqual(fresh.get_grace_period_days(), 3)

    def test_zero_disables(self):
        self.manager.set_grace_period_days(3)
        self.manager.set_grace_period_days(0)
        self.assertEqual(self.manager.get_grace_period_days(), 0)

    def test_out_of_range_rejected(self):
        with self.assertRaises(ConfigValidationError):
            self.manager.set_grace_period_days(31)
        with self.assertRaises(ConfigValidationError):
            self.manager.set_grace_period_days(-1)

    def test_type_coercion(self):
        self.manager.set_grace_period_days("7")
        self.assertEqual(self.manager.get_grace_period_days(), 7)

    def test_validation_limits_registered(self):
        self.assertEqual(validate_config_value("grace_period_days", 30), 30)
        with self.assertRaises(ConfigValidationError):
            validate_config_value("grace_period_days", "abc")


if __name__ == "__main__":
    unittest.main()
