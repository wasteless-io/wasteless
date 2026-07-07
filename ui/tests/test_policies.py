#!/usr/bin/env python3
"""
Unit tests for utils/policies.py — policy-as-code export / import
validation.
"""

import sys
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from utils.config_manager import ConfigValidationError
from utils.policies import (
    export_policy_yaml,
    parse_policy_yaml,
    validate_policy,
)


def valid_policy():
    return {
        "auto_remediation": {
            "enabled": False,
            "dry_run_days": 7,
            "actions": {"stop_instance": True, "delete_volume": False},
        },
        "approval": {"grace_period_days": 3},
        "protection": {
            "min_instance_age_days": 30,
            "min_idle_days": 14,
            "min_confidence_score": 0.8,
            "max_instances_per_run": 3,
        },
        "whitelist": {
            "instance_ids": ["i-0123456789abcdef0"],
            "tags": [{"key": "Environment", "value": "Production"}],
        },
        "schedule": {
            "enabled": False,
            "allowed_days": ["Saturday"],
            "allowed_hours": [2, 3],
            "timezone": "UTC",
        },
        "dry_run": True,
    }


class TestValidatePolicy(unittest.TestCase):

    def test_valid_policy_accepted(self):
        config = validate_policy(valid_policy())
        self.assertEqual(config["approval"]["grace_period_days"], 3)

    def test_not_a_mapping_rejected(self):
        with self.assertRaises(ConfigValidationError):
            validate_policy(["a", "list"])

    def test_unknown_section_rejected(self):
        policy = valid_policy()
        policy["malicious_section"] = {"x": 1}
        with self.assertRaisesRegex(ConfigValidationError, "unknown section"):
            validate_policy(policy)

    def test_wrong_section_type_rejected(self):
        policy = valid_policy()
        policy["protection"] = "not a dict"
        with self.assertRaisesRegex(ConfigValidationError, "must be a dict"):
            validate_policy(policy)

    def test_out_of_range_grace_period_rejected(self):
        policy = valid_policy()
        policy["approval"]["grace_period_days"] = 99
        with self.assertRaisesRegex(ConfigValidationError, "grace_period_days"):
            validate_policy(policy)

    def test_out_of_range_confidence_rejected(self):
        policy = valid_policy()
        policy["protection"]["min_confidence_score"] = 1.5
        with self.assertRaises(ConfigValidationError):
            validate_policy(policy)

    def test_numeric_strings_normalized(self):
        policy = valid_policy()
        policy["approval"]["grace_period_days"] = "5"
        config = validate_policy(policy)
        self.assertEqual(config["approval"]["grace_period_days"], 5)

    def test_invalid_instance_id_rejected(self):
        policy = valid_policy()
        policy["whitelist"]["instance_ids"] = ["not-an-id"]
        with self.assertRaisesRegex(ConfigValidationError, "whitelist"):
            validate_policy(policy)

    def test_invalid_allowed_hours_rejected(self):
        policy = valid_policy()
        policy["schedule"]["allowed_hours"] = [25]
        with self.assertRaisesRegex(ConfigValidationError, "allowed_hours"):
            validate_policy(policy)

    def test_non_boolean_action_toggle_rejected(self):
        policy = valid_policy()
        policy["auto_remediation"]["actions"] = {"stop_instance": "yes"}
        with self.assertRaisesRegex(ConfigValidationError, "actions"):
            validate_policy(policy)

    def test_all_errors_reported_at_once(self):
        policy = valid_policy()
        policy["bogus"] = {}
        policy["approval"]["grace_period_days"] = 99
        try:
            validate_policy(policy)
            self.fail("expected ConfigValidationError")
        except ConfigValidationError as e:
            self.assertIn("bogus", str(e))
            self.assertIn("grace_period_days", str(e))


class TestParsePolicyYaml(unittest.TestCase):

    def test_valid_yaml_parsed(self):
        config = parse_policy_yaml(yaml.safe_dump(valid_policy()))
        self.assertTrue(config["dry_run"])

    def test_malformed_yaml_rejected(self):
        with self.assertRaisesRegex(ConfigValidationError, "invalid YAML"):
            parse_policy_yaml("key: [unclosed")

    def test_empty_document_rejected(self):
        with self.assertRaisesRegex(ConfigValidationError, "empty"):
            parse_policy_yaml("")


class TestExportRoundtrip(unittest.TestCase):

    def test_export_has_header_and_reimports_identically(self):
        policy = valid_policy()
        text = export_policy_yaml(policy)
        self.assertTrue(text.startswith("# Wasteless remediation policy"))
        self.assertEqual(parse_policy_yaml(text), policy)


if __name__ == "__main__":
    unittest.main()
