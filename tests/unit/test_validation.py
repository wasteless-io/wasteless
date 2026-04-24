"""
Unit tests for parameter validation functions.
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from detectors.ec2_idle import (
    validate_cpu_threshold,
    validate_days,
    ValidationError,
    EC2_PRICING,
    DEFAULT_INSTANCE_COST_EUR
)


class TestValidateCpuThreshold:
    """Tests for CPU threshold validation."""

    def test_valid_threshold(self):
        """Valid thresholds should not raise."""
        validate_cpu_threshold(5.0)
        validate_cpu_threshold(1.0)
        validate_cpu_threshold(50.0)
        validate_cpu_threshold(99.9)

    def test_valid_threshold_integer(self):
        """Integer thresholds should be accepted."""
        validate_cpu_threshold(5)
        validate_cpu_threshold(10)

    def test_zero_threshold_raises(self):
        """Zero threshold should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_cpu_threshold(0)
        assert "between 0 and 100" in str(exc_info.value)

    def test_negative_threshold_raises(self):
        """Negative threshold should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_cpu_threshold(-5.0)
        assert "between 0 and 100" in str(exc_info.value)

    def test_over_100_threshold_raises(self):
        """Threshold > 100 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_cpu_threshold(101.0)
        assert "between 0 and 100" in str(exc_info.value)

    def test_exactly_100_is_valid(self):
        """Threshold of exactly 100 should be valid."""
        validate_cpu_threshold(100.0)

    def test_string_threshold_raises(self):
        """String threshold should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_cpu_threshold("5.0")
        assert "must be a number" in str(exc_info.value)

    def test_none_threshold_raises(self):
        """None threshold should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_cpu_threshold(None)
        assert "must be a number" in str(exc_info.value)


class TestValidateDays:
    """Tests for days parameter validation."""

    def test_valid_days(self):
        """Valid days should not raise."""
        validate_days(1)
        validate_days(7)
        validate_days(30)
        validate_days(365)

    def test_zero_days_raises(self):
        """Zero days should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_days(0)
        assert "must be a positive integer" in str(exc_info.value)

    def test_negative_days_raises(self):
        """Negative days should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_days(-7)
        assert "must be a positive integer" in str(exc_info.value)

    def test_over_365_days_raises(self):
        """Days > 365 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_days(366)
        assert "cannot exceed 365" in str(exc_info.value)

    def test_float_days_raises(self):
        """Float days should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_days(7.5)
        assert "must be an integer" in str(exc_info.value)

    def test_string_days_raises(self):
        """String days should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_days("7")
        assert "must be an integer" in str(exc_info.value)


class TestEC2Pricing:
    """Tests for EC2 pricing data."""

    def test_pricing_dict_not_empty(self):
        """Pricing dictionary should not be empty."""
        assert len(EC2_PRICING) > 0

    def test_common_instance_types_exist(self):
        """Common instance types should have pricing."""
        common_types = ['t3.micro', 't3.small', 't3.medium', 'm5.large']
        for instance_type in common_types:
            assert instance_type in EC2_PRICING, f"{instance_type} not in pricing"

    def test_all_prices_positive(self):
        """All prices should be positive."""
        for instance_type, price in EC2_PRICING.items():
            assert price > 0, f"{instance_type} has non-positive price: {price}"

    def test_default_cost_is_reasonable(self):
        """Default cost should be reasonable (between 10 and 500 EUR)."""
        assert 10 < DEFAULT_INSTANCE_COST_EUR < 500
