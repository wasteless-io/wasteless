#!/usr/bin/env python3
"""
Policy-as-code for Wasteless
=============================

Export / import of the remediation policy (config/remediation.yaml) as a
YAML document, so the whole safeguard policy can be versioned in git,
reviewed in a PR, and re-imported — without giving up the UI.

Import is strictly validated: unknown top-level sections, wrong types or
out-of-range values are rejected before anything is written.
"""

import copy
from datetime import date
from typing import Any, Dict, List

import yaml

from utils.config_manager import (
    ConfigValidationError,
    validate_config_value,
    validate_instance_id,
)

# Top-level sections accepted in a policy document, with the expected type.
KNOWN_SECTIONS = {
    "auto_remediation": dict,
    "approval": dict,
    "protection": dict,
    "whitelist": dict,
    "schedule": dict,
    "notifications": dict,
    "rollback": dict,
    "logging": dict,
    "aws": dict,
    "terraform_pr": dict,
    "dry_run": bool,
}

# Numeric keys validated against CONFIG_LIMITS (section, key)
_LIMITED_KEYS = [
    ("auto_remediation", "dry_run_days"),
    ("approval", "grace_period_days"),
    ("protection", "min_instance_age_days"),
    ("protection", "min_idle_days"),
    ("protection", "min_confidence_score"),
    ("protection", "max_instances_per_run"),
]

EXPORT_HEADER = """\
# Wasteless remediation policy — exported {today}
# Version this file in git; re-import it from Settings > Policy as code
# or POST /api/policies/import. Unknown keys are rejected at import.
"""


def export_policy_yaml(config: Dict[str, Any]) -> str:
    """Serialize the current policy to a commented YAML document."""
    return EXPORT_HEADER.format(today=date.today().isoformat()) + yaml.safe_dump(
        config, default_flow_style=False, sort_keys=False, allow_unicode=True
    )


def validate_policy(data: Any) -> Dict[str, Any]:
    """Validate an imported policy document.

    Returns the validated (normalized) config dict, or raises
    ConfigValidationError listing every problem found.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        raise ConfigValidationError(f"policy must be a YAML mapping, got {type(data).__name__}")

    for key, value in data.items():
        expected = KNOWN_SECTIONS.get(key)
        if expected is None:
            errors.append(f"unknown section: {key!r}")
        elif not isinstance(value, expected):
            errors.append(
                f"section {key!r} must be a {expected.__name__}, " f"got {type(value).__name__}"
            )

    config = copy.deepcopy(data)

    for section, key in _LIMITED_KEYS:
        if isinstance(config.get(section), dict) and key in config[section]:
            try:
                config[section][key] = validate_config_value(key, config[section][key])
            except ConfigValidationError as e:
                errors.append(str(e))

    if isinstance(config.get("auto_remediation"), dict):
        enabled = config["auto_remediation"].get("enabled", False)
        if not isinstance(enabled, bool):
            errors.append("auto_remediation.enabled must be a boolean")
        actions = config["auto_remediation"].get("actions", {})
        if not isinstance(actions, dict) or not all(isinstance(v, bool) for v in actions.values()):
            errors.append("auto_remediation.actions must map action names to booleans")

    if isinstance(config.get("whitelist"), dict):
        ids = config["whitelist"].get("instance_ids", [])
        if not isinstance(ids, list):
            errors.append("whitelist.instance_ids must be a list")
        else:
            for iid in ids:
                try:
                    validate_instance_id(iid)
                except (ConfigValidationError, ValueError) as e:
                    errors.append(f"whitelist.instance_ids: {e}")
        tags = config["whitelist"].get("tags", [])
        if not isinstance(tags, list):
            errors.append("whitelist.tags must be a list")

    if isinstance(config.get("schedule"), dict):
        hours = config["schedule"].get("allowed_hours", [])
        if not isinstance(hours, list) or not all(
            isinstance(h, int) and 0 <= h <= 23 for h in hours
        ):
            errors.append("schedule.allowed_hours must be a list of hours (0-23)")
        days = config["schedule"].get("allowed_days", [])
        if not isinstance(days, list):
            errors.append("schedule.allowed_days must be a list")

    if errors:
        raise ConfigValidationError("; ".join(errors))
    return config


def parse_policy_yaml(text: str) -> Dict[str, Any]:
    """Parse and validate a policy YAML document."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigValidationError(f"invalid YAML: {e}")
    if data is None:
        raise ConfigValidationError("policy document is empty")
    return validate_policy(data)
