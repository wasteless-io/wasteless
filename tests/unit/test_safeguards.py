"""
Unit tests for safeguards module.
"""

import pytest
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.safeguards import Safeguards, SafeguardException, InvalidSafeguardConfig


class TestSafeguardException:
    """Tests for SafeguardException."""

    def test_exception_inherits_from_exception(self):
        """SafeguardException should inherit from Exception."""
        assert issubclass(SafeguardException, Exception)

    def test_exception_message(self):
        """SafeguardException should preserve message."""
        exc = SafeguardException("Test message")
        assert str(exc) == "Test message"


class TestActionToggles:
    """Per-action opt-out (auto_remediation.actions)."""

    def _safeguards(self, config):
        with patch.object(Safeguards, "_load_config", return_value=config):
            return Safeguards()

    def test_missing_map_means_enabled(self):
        sg = self._safeguards({"auto_remediation": {"enabled": True}})
        assert sg.is_action_enabled("delete_nat_gateway") is True

    def test_missing_type_means_enabled(self):
        sg = self._safeguards(
            {"auto_remediation": {"enabled": True, "actions": {"stop_instance": True}}}
        )
        assert sg.is_action_enabled("migrate_gp2_to_gp3") is True

    def test_disabled_type(self):
        sg = self._safeguards(
            {"auto_remediation": {"enabled": True, "actions": {"delete_nat_gateway": False}}}
        )
        assert sg.is_action_enabled("delete_nat_gateway") is False
        assert sg.is_action_enabled("stop_instance") is True


class TestSafeguardsWhitelist:
    """Tests for whitelist functionality."""

    @pytest.fixture
    def mock_config(self):
        """Return a mock configuration."""
        return {
            "auto_remediation": {"enabled": True},
            "whitelist": {
                "instance_ids": ["i-protected123", "i-critical456"],
                "tags": [
                    {"key": "Environment", "value": "Production"},
                    {"key": "Critical", "value": "true"},
                ],
            },
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }

    @pytest.fixture
    def safeguards(self, mock_config):
        """Create Safeguards instance with mock config."""
        with patch.object(Safeguards, "_load_config", return_value=mock_config):
            return Safeguards()

    def test_whitelisted_by_instance_id(self, safeguards):
        """Instance in whitelist should be detected."""
        assert safeguards.is_whitelisted("i-protected123", {}) is True
        assert safeguards.is_whitelisted("i-critical456", {}) is True

    def test_not_whitelisted_by_instance_id(self, safeguards):
        """Instance not in whitelist should not be detected."""
        assert safeguards.is_whitelisted("i-random789", {}) is False

    def test_whitelisted_by_production_tag(self, safeguards):
        """Instance with Production tag should be whitelisted."""
        tags = {"Environment": "Production", "Name": "MyServer"}
        assert safeguards.is_whitelisted("i-any123", tags) is True

    def test_whitelisted_by_critical_tag(self, safeguards):
        """Instance with Critical=true tag should be whitelisted."""
        tags = {"Critical": "true", "Name": "ImportantServer"}
        assert safeguards.is_whitelisted("i-any123", tags) is True

    def test_not_whitelisted_by_dev_environment(self, safeguards):
        """Instance with dev environment should not be whitelisted."""
        tags = {"Environment": "dev", "Name": "DevServer"}
        assert safeguards.is_whitelisted("i-dev123", tags) is False


class TestSafeguardsAgeCheck:
    """Tests for instance age checking."""

    @pytest.fixture
    def mock_config(self):
        """Return a mock configuration."""
        return {
            "auto_remediation": {"enabled": True},
            "whitelist": {"instance_ids": [], "tags": []},
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }

    @pytest.fixture
    def safeguards(self, mock_config):
        """Create Safeguards instance with mock config."""
        with patch.object(Safeguards, "_load_config", return_value=mock_config):
            return Safeguards()

    def test_old_instance_passes(self, safeguards):
        """Instance older than min age should pass."""
        launch_time = datetime.now(timezone.utc) - timedelta(days=60)
        assert safeguards.check_instance_age(launch_time) is True

    def test_young_instance_fails(self, safeguards):
        """Instance younger than min age should fail."""
        launch_time = datetime.now() - timedelta(days=10)

        with pytest.raises(SafeguardException) as exc_info:
            safeguards.check_instance_age(launch_time)

        assert "too young" in str(exc_info.value)


class TestSafeguardsConfidence:
    """Tests for confidence score checking."""

    @pytest.fixture
    def mock_config(self):
        """Return a mock configuration with 0.80 min confidence."""
        return {
            "auto_remediation": {"enabled": True},
            "whitelist": {"instance_ids": [], "tags": []},
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }

    @pytest.fixture
    def safeguards(self, mock_config):
        """Create Safeguards instance with mock config."""
        with patch.object(Safeguards, "_load_config", return_value=mock_config):
            return Safeguards()

    def test_high_confidence_passes(self, safeguards):
        """High confidence should pass."""
        assert safeguards.check_confidence_score(0.90) is True
        assert safeguards.check_confidence_score(0.85) is True
        assert safeguards.check_confidence_score(0.80) is True

    def test_low_confidence_fails(self, safeguards):
        """Low confidence should fail."""
        with pytest.raises(SafeguardException) as exc_info:
            safeguards.check_confidence_score(0.70)

        assert "too low" in str(exc_info.value)

    def test_borderline_confidence(self, safeguards):
        """Borderline confidence (just below threshold) should fail."""
        with pytest.raises(SafeguardException):
            safeguards.check_confidence_score(0.79)


class TestSafeguardsIdleDuration:
    """Tests for idle duration checking."""

    @pytest.fixture
    def mock_config(self):
        """Return a mock configuration with 14 days min idle."""
        return {
            "auto_remediation": {"enabled": True},
            "whitelist": {"instance_ids": [], "tags": []},
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }

    @pytest.fixture
    def safeguards(self, mock_config):
        """Create Safeguards instance with mock config."""
        with patch.object(Safeguards, "_load_config", return_value=mock_config):
            return Safeguards()

    def test_long_idle_passes(self, safeguards):
        """Long idle duration should pass."""
        assert safeguards.check_idle_duration(20) is True
        assert safeguards.check_idle_duration(14) is True

    def test_short_idle_fails(self, safeguards):
        """Short idle duration should fail."""
        with pytest.raises(SafeguardException) as exc_info:
            safeguards.check_idle_duration(7)

        assert "Not idle long enough" in str(exc_info.value)


class TestSafeguardsMaxInstances:
    """Tests for max instances limit."""

    @pytest.fixture
    def mock_config(self):
        """Return a mock configuration with max 3 instances per run."""
        return {
            "auto_remediation": {"enabled": True},
            "whitelist": {"instance_ids": [], "tags": []},
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }

    @pytest.fixture
    def safeguards(self, mock_config):
        """Create Safeguards instance with mock config."""
        with patch.object(Safeguards, "_load_config", return_value=mock_config):
            return Safeguards()

    def test_under_limit_passes(self, safeguards):
        """Count under limit should pass."""
        assert safeguards.check_max_instances_limit(0) is True
        assert safeguards.check_max_instances_limit(1) is True
        assert safeguards.check_max_instances_limit(2) is True

    def test_at_limit_fails(self, safeguards):
        """Count at limit should fail."""
        with pytest.raises(SafeguardException) as exc_info:
            safeguards.check_max_instances_limit(3)

        assert "limit reached" in str(exc_info.value)

    def test_over_limit_fails(self, safeguards):
        """Count over limit should fail."""
        with pytest.raises(SafeguardException):
            safeguards.check_max_instances_limit(5)


class TestSafeguardsSchedule:
    """Tests for schedule check, including the enabled flag."""

    def _safeguards(self, schedule):
        config = {"schedule": schedule}
        with patch.object(Safeguards, "_load_config", return_value=config):
            return Safeguards()

    def test_disabled_schedule_always_passes(self):
        """schedule.enabled=false must bypass day/hour restrictions."""
        sg = self._safeguards(
            {
                "enabled": False,
                "allowed_days": ["Saturday", "Sunday"],
                "allowed_hours": [2, 3, 4],
            }
        )
        assert sg.check_schedule() is True

    def test_missing_enabled_key_defaults_to_disabled(self):
        sg = self._safeguards(
            {
                "allowed_days": ["Saturday"],
                "allowed_hours": [2],
            }
        )
        assert sg.check_schedule() is True

    def test_enabled_schedule_blocks_wrong_day(self):
        sg = self._safeguards(
            {
                "enabled": True,
                "allowed_days": ["NoSuchDay"],
                "allowed_hours": [],
            }
        )
        with pytest.raises(SafeguardException) as exc_info:
            sg.check_schedule()
        assert "Outside allowed schedule" in str(exc_info.value)

    def test_enabled_schedule_without_restrictions_passes(self):
        sg = self._safeguards(
            {
                "enabled": True,
                "allowed_days": [],
                "allowed_hours": [],
            }
        )
        assert sg.check_schedule() is True


class TestSafeguardConfigValidation:
    """A malformed remediation.yaml must fail loudly (InvalidSafeguardConfig),
    not silently loosen a check's threshold."""

    def test_valid_config_passes(self):
        Safeguards._validate_config(
            {
                "protection": {
                    "min_instance_age_days": 30,
                    "min_idle_days": 14,
                    "min_confidence_score": 0.8,
                    "max_instances_per_run": 3,
                },
                "whitelist": {
                    "instance_ids": ["i-abc123"],
                    "tags": [{"key": "Environment", "value": "Production"}],
                },
                "schedule": {
                    "allowed_days": ["Saturday", "Sunday"],
                    "allowed_hours": [2, 3, 4],
                },
            }
        )  # must not raise

    def test_empty_config_passes(self):
        Safeguards._validate_config({})  # all sections optional, defaults apply

    def test_confidence_score_above_one_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"protection": {"min_confidence_score": 8.0}})

    def test_negative_confidence_score_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"protection": {"min_confidence_score": -1}})

    def test_confidence_score_as_string_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"protection": {"min_confidence_score": "0.8"}})

    def test_negative_idle_days_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"protection": {"min_idle_days": -5}})

    def test_max_instances_per_run_zero_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"protection": {"max_instances_per_run": 0}})

    def test_whitelist_instance_ids_not_a_list_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"whitelist": {"instance_ids": "i-abc123"}})

    def test_whitelist_tag_missing_value_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"whitelist": {"tags": [{"key": "Environment"}]}})

    def test_schedule_invalid_weekday_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"schedule": {"allowed_days": ["Someday"]}})

    def test_schedule_hour_out_of_range_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"schedule": {"allowed_hours": [24]}})

    def test_schedule_hour_as_bool_rejected(self):
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards._validate_config({"schedule": {"allowed_hours": [True]}})

    def test_malformed_config_blocks_init(self, tmp_path):
        """A real remediation.yaml with an out-of-range value must fail at
        Safeguards() construction, not at first check_confidence_score() call."""
        bad_config = tmp_path / "remediation.yaml"
        bad_config.write_text("protection:\n  min_confidence_score: -1\n")
        with pytest.raises(InvalidSafeguardConfig):
            Safeguards(config_path=str(bad_config))


class TestValidateAll:
    """Regression tests for the validate_all orchestrator.

    validate_all() is the only place all 7 safeguard checks run together
    in their required order with short-circuit on first failure -- the
    individual check_* tests above can't catch a check being dropped,
    reordered, or no longer short-circuiting. A good instance is
    deliberately made to fail every downstream check at once, so each
    test also proves the earlier checks run *first* and stop the chain.
    """

    GOOD_TAGS: Dict = {"Environment": "dev"}
    GOOD_LAUNCH_TIME = datetime.now(timezone.utc) - timedelta(days=60)
    GOOD_CONFIDENCE = 0.90
    GOOD_IDLE_DAYS = 20
    GOOD_COUNT = 0

    def _safeguards(self, overrides: Optional[Dict] = None):
        config = {
            "auto_remediation": {"enabled": True},
            "whitelist": {"instance_ids": [], "tags": []},
            "protection": {
                "min_instance_age_days": 30,
                "min_confidence_score": 0.80,
                "min_idle_days": 14,
                "max_instances_per_run": 3,
            },
            "schedule": {"allowed_days": [], "allowed_hours": []},
        }
        for key, value in (overrides or {}).items():
            config[key] = value
        with patch.object(Safeguards, "_load_config", return_value=config):
            return Safeguards()

    def test_all_checks_pass(self):
        sg = self._safeguards()
        result = sg.validate_all(
            instance_id="i-good123",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=self.GOOD_IDLE_DAYS,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is True
        assert result["reason"] is None
        assert result["checks_failed"] == []
        assert result["checks_passed"] == [
            "whitelist",
            "instance_age",
            "confidence",
            "idle_duration",
            "schedule",
            "max_instances",
        ]

    def test_global_disabled_short_circuits_before_every_other_check(self):
        """Even with an otherwise-perfect instance, the global kill switch
        must be the very first thing evaluated."""
        sg = self._safeguards(overrides={"auto_remediation": {"enabled": False}})
        result = sg.validate_all(
            instance_id="i-good123",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=self.GOOD_IDLE_DAYS,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == []
        assert result["checks_failed"] == ["global_enabled"]
        # Le message doit rester actionnable : nommer l'interrupteur et le
        # chemin pour l'activer, pas seulement constater le refus.
        assert "Auto-remediation is disabled" in result["reason"]
        assert "Settings" in result["reason"]
        assert "auto_remediation.enabled" in result["reason"]

    def test_whitelisted_short_circuits_before_age_confidence_idle(self):
        sg = self._safeguards(
            overrides={"whitelist": {"instance_ids": ["i-protected"], "tags": []}}
        )
        result = sg.validate_all(
            instance_id="i-protected",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=self.GOOD_IDLE_DAYS,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == []
        assert result["checks_failed"] == ["whitelist"]
        assert result["reason"] == "Instance is whitelisted"

    def test_young_instance_fails_before_confidence_and_idle_are_checked(self):
        """launch_time is too young AND confidence/idle are also bad -- if
        age stops the chain first, checks_passed must stop at 'whitelist'."""
        sg = self._safeguards()
        result = sg.validate_all(
            instance_id="i-young",
            instance_tags=self.GOOD_TAGS,
            launch_time=datetime.now(timezone.utc) - timedelta(days=5),
            confidence=0.10,
            idle_days=1,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == ["whitelist"]
        assert "too young" in result["reason"]

    def test_low_confidence_fails_before_idle_is_checked(self):
        sg = self._safeguards()
        result = sg.validate_all(
            instance_id="i-unsure",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=0.10,
            idle_days=1,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == ["whitelist", "instance_age"]
        assert "Confidence too low" in result["reason"]

    def test_short_idle_fails_before_schedule_is_checked(self):
        sg = self._safeguards(
            overrides={
                "schedule": {"enabled": True, "allowed_days": ["NoSuchDay"], "allowed_hours": []}
            }
        )
        result = sg.validate_all(
            instance_id="i-recent",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=1,
            current_count=self.GOOD_COUNT,
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == ["whitelist", "instance_age", "confidence"]
        assert "Not idle long enough" in result["reason"]

    def test_schedule_fails_before_max_instances_is_checked(self):
        sg = self._safeguards(
            overrides={
                "schedule": {"enabled": True, "allowed_days": ["NoSuchDay"], "allowed_hours": []}
            }
        )
        result = sg.validate_all(
            instance_id="i-offhours",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=self.GOOD_IDLE_DAYS,
            current_count=99,  # would also fail max_instances
        )
        assert result["safe_to_proceed"] is False
        assert result["checks_passed"] == [
            "whitelist",
            "instance_age",
            "confidence",
            "idle_duration",
        ]
        assert "Outside allowed schedule" in result["reason"]

    def test_max_instances_limit_fails_as_final_check(self):
        sg = self._safeguards(overrides={"protection": {"max_instances_per_run": 3}})
        result = sg.validate_all(
            instance_id="i-toomany",
            instance_tags=self.GOOD_TAGS,
            launch_time=self.GOOD_LAUNCH_TIME,
            confidence=self.GOOD_CONFIDENCE,
            idle_days=self.GOOD_IDLE_DAYS,
            current_count=3,
        )
        assert result["safe_to_proceed"] is False
        assert (
            result["checks_passed"]
            == [
                "whitelist",
                "instance_age",
                "confidence",
                "idle_duration",
                "schedule",
                "max_instances",
            ][:5]
        )
        assert "Max instances limit reached" in result["reason"]
