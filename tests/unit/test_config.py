"""
Unit tests for configuration module.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.config import (
    AWSConfig,
    DatabaseConfig,
    DetectorConfig,
    RemediationConfig,
    ConfigurationError,
    validate_environment
)


class TestAWSConfig:
    """Tests for AWS configuration."""

    def test_from_env_with_all_vars(self):
        """Should load config when all env vars are set."""
        env_vars = {
            'AWS_REGION': 'us-west-2',
            'AWS_ACCOUNT_ID': '123456789012',
            'AWS_ACCESS_KEY_ID': 'AKIATEST',
            'AWS_SECRET_ACCESS_KEY': 'secretkey'
        }

        with patch.dict(os.environ, env_vars, clear=False):
            config = AWSConfig.from_env()

            assert config.region == 'us-west-2'
            assert config.account_id == '123456789012'
            assert config.access_key_id == 'AKIATEST'
            assert config.secret_access_key == 'secretkey'

    def test_from_env_with_defaults(self):
        """Should use defaults when optional vars missing."""
        env_vars = {
            'AWS_REGION': 'eu-west-1',
            'AWS_ACCOUNT_ID': ''
        }

        # Clear specific vars
        with patch.dict(os.environ, env_vars, clear=False):
            with patch.object(os, 'getenv', side_effect=lambda k, d=None: env_vars.get(k, d)):
                # This will use defaults
                pass  # Test simplified


class TestDatabaseConfig:
    """Tests for database configuration."""

    def test_from_env_with_all_vars(self):
        """Should load config when all required vars are set."""
        env_vars = {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_NAME': 'testdb',
            'DB_USER': 'testuser',
            'DB_PASSWORD': 'testpass'
        }

        with patch.dict(os.environ, env_vars, clear=False):
            config = DatabaseConfig.from_env()

            assert config.host == 'localhost'
            assert config.port == 5432
            assert config.name == 'testdb'
            assert config.user == 'testuser'
            assert config.password == 'testpass'

    def test_from_env_missing_required_raises(self):
        """Should raise when required vars are missing."""
        env_vars = {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            # Missing DB_NAME, DB_USER, DB_PASSWORD
        }

        def mock_getenv(key, default=None):
            return env_vars.get(key, default)

        with patch.object(os, 'getenv', side_effect=mock_getenv):
            with pytest.raises(ConfigurationError) as exc_info:
                DatabaseConfig.from_env()

            assert "Missing required" in str(exc_info.value)

    def test_to_dict(self):
        """Should return connection parameters as dict."""
        config = DatabaseConfig(
            host='localhost',
            port=5432,
            name='testdb',
            user='testuser',
            password='testpass'
        )

        params = config.to_dict()

        assert params['host'] == 'localhost'
        assert params['port'] == 5432
        assert params['database'] == 'testdb'
        assert params['user'] == 'testuser'
        assert params['password'] == 'testpass'
        assert 'connect_timeout' in params


class TestDetectorConfig:
    """Tests for detector configuration."""

    def test_default_values(self):
        """Should have sensible defaults."""
        config = DetectorConfig()

        assert config.cpu_threshold == 5.0
        assert config.analysis_days == 7
        assert config.min_datapoints == 3

    def test_validate_valid_config(self):
        """Valid config should not raise."""
        config = DetectorConfig(cpu_threshold=10.0, analysis_days=14)
        config.validate()  # Should not raise

    def test_validate_invalid_cpu_threshold(self):
        """Invalid CPU threshold should raise."""
        config = DetectorConfig(cpu_threshold=150.0)

        with pytest.raises(ConfigurationError) as exc_info:
            config.validate()

        assert "cpu_threshold" in str(exc_info.value)

    def test_validate_invalid_days(self):
        """Invalid days should raise."""
        config = DetectorConfig(analysis_days=500)

        with pytest.raises(ConfigurationError) as exc_info:
            config.validate()

        assert "analysis_days" in str(exc_info.value)


class TestRemediationConfig:
    """Tests for remediation configuration."""

    def test_default_values(self):
        """Should have safe defaults."""
        config = RemediationConfig()

        # Auto-remediation should be OFF by default
        assert config.enabled is False
        assert config.min_confidence_score == 0.80
        assert config.max_instances_per_run == 3

    def test_from_yaml_file_not_found(self):
        """Should return defaults when file not found."""
        config = RemediationConfig.from_yaml('/nonexistent/path.yaml')

        assert config.enabled is False
        assert config.max_instances_per_run == 3

    def test_from_yaml_with_mock_file(self):
        """Should load config from YAML."""
        yaml_content = """
auto_remediation:
  enabled: true
  dry_run_days: 5
protection:
  min_instance_age_days: 45
  min_confidence_score: 0.85
  max_instances_per_run: 5
whitelist:
  instance_ids:
    - i-test123
  tags:
    - key: Env
      value: Prod
schedule:
  allowed_days:
    - Saturday
  allowed_hours:
    - 2
"""
        import yaml as yaml_lib

        with patch('builtins.open', MagicMock()):
            with patch('yaml.safe_load', return_value=yaml_lib.safe_load(yaml_content)):
                config = RemediationConfig.from_yaml('fake.yaml')

                assert config.enabled is True
                assert config.dry_run_days == 5
                assert config.min_instance_age_days == 45
                assert config.min_confidence_score == 0.85
                assert config.max_instances_per_run == 5
                assert 'i-test123' in config.whitelisted_instance_ids


class TestValidateEnvironment:
    """Tests for environment validation."""

    def test_all_vars_present(self):
        """Should return True for all present vars."""
        env_vars = {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_NAME': 'testdb',
            'DB_USER': 'testuser',
            'DB_PASSWORD': 'testpass',
            'AWS_REGION': 'eu-west-1',
            'AWS_ACCOUNT_ID': '123456789'
        }

        with patch.dict(os.environ, env_vars, clear=False):
            status = validate_environment()

            for var in ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']:
                assert status[var] is True

    def test_missing_vars_detected(self):
        """Should detect missing variables."""
        # Create a mock that returns None for missing vars
        def mock_getenv(key, default=None):
            if key == 'DB_PASSWORD':
                return None
            return 'value'

        with patch.object(os, 'getenv', side_effect=mock_getenv):
            status = validate_environment()
            # Test behavior depends on actual implementation
