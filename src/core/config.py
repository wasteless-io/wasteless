"""
Centralized Configuration Management for Wasteless.

Provides a single source of truth for all configuration values,
environment variables, and application settings.
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv
import yaml

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Exception raised for configuration errors."""
    pass


@dataclass
class AWSConfig:
    """AWS-related configuration."""
    region: str
    account_id: str
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None

    @classmethod
    def from_env(cls) -> 'AWSConfig':
        """Load AWS config from environment variables."""
        region = os.getenv('AWS_REGION', 'eu-west-1')
        account_id = os.getenv('AWS_ACCOUNT_ID', '')

        if not account_id:
            logger.warning("AWS_ACCOUNT_ID not set, some features may not work")

        return cls(
            region=region,
            account_id=account_id,
            access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        )


@dataclass
class DatabaseConfig:
    """Database configuration."""
    host: str
    port: int
    name: str
    user: str
    password: str
    min_connections: int = 2
    max_connections: int = 10
    connect_timeout: int = 10

    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        """Load database config from environment variables."""
        required = ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing = [var for var in required if not os.getenv(var)]

        if missing:
            raise ConfigurationError(
                f"Missing required database environment variables: {', '.join(missing)}"
            )

        return cls(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT')),
            name=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            min_connections=int(os.getenv('DB_MIN_CONNECTIONS', '2')),
            max_connections=int(os.getenv('DB_MAX_CONNECTIONS', '10')),
            connect_timeout=int(os.getenv('DB_CONNECT_TIMEOUT', '10')),
        )

    def to_dsn(self) -> str:
        """Return database connection string."""
        return (
            f"host={self.host} port={self.port} dbname={self.name} "
            f"user={self.user} password={self.password}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return connection parameters as dictionary."""
        return {
            'host': self.host,
            'port': self.port,
            'database': self.name,
            'user': self.user,
            'password': self.password,
            'connect_timeout': self.connect_timeout,
        }


@dataclass
class DetectorConfig:
    """Configuration for waste detection."""
    cpu_threshold: float = 5.0
    analysis_days: int = 7
    min_datapoints: int = 3

    def validate(self) -> None:
        """Validate detector configuration."""
        if not 0 < self.cpu_threshold <= 100:
            raise ConfigurationError(
                f"cpu_threshold must be between 0 and 100, got {self.cpu_threshold}"
            )
        if self.analysis_days <= 0 or self.analysis_days > 365:
            raise ConfigurationError(
                f"analysis_days must be between 1 and 365, got {self.analysis_days}"
            )


@dataclass
class RemediationConfig:
    """Configuration for auto-remediation."""
    enabled: bool = False
    dry_run_days: int = 7
    min_instance_age_days: int = 30
    min_idle_days: int = 14
    min_confidence_score: float = 0.80
    max_instances_per_run: int = 3
    whitelisted_instance_ids: list = field(default_factory=list)
    whitelisted_tags: list = field(default_factory=list)
    allowed_days: list = field(default_factory=list)
    allowed_hours: list = field(default_factory=list)

    @classmethod
    def from_yaml(cls, config_path: str = "config/remediation.yaml") -> 'RemediationConfig':
        """Load remediation config from YAML file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file {config_path} not found, using defaults")
            return cls()
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in {config_path}: {e}")

        auto_rem = config.get('auto_remediation', {})
        protection = config.get('protection', {})
        whitelist = config.get('whitelist', {})
        schedule = config.get('schedule', {})

        return cls(
            enabled=auto_rem.get('enabled', False),
            dry_run_days=auto_rem.get('dry_run_days', 7),
            min_instance_age_days=protection.get('min_instance_age_days', 30),
            min_idle_days=protection.get('min_idle_days', 14),
            min_confidence_score=protection.get('min_confidence_score', 0.80),
            max_instances_per_run=protection.get('max_instances_per_run', 3),
            whitelisted_instance_ids=whitelist.get('instance_ids', []),
            whitelisted_tags=whitelist.get('tags', []),
            allowed_days=schedule.get('allowed_days', []),
            allowed_hours=schedule.get('allowed_hours', []),
        )


@dataclass
class AppConfig:
    """Main application configuration container."""
    aws: AWSConfig
    database: DatabaseConfig
    detector: DetectorConfig
    remediation: RemediationConfig
    log_level: str = "INFO"
    dry_run: bool = True

    @classmethod
    def load(cls, remediation_config_path: str = "config/remediation.yaml") -> 'AppConfig':
        """Load complete application configuration."""
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        dry_run = os.getenv('DRY_RUN', 'true').lower() in ('true', '1', 'yes')

        return cls(
            aws=AWSConfig.from_env(),
            database=DatabaseConfig.from_env(),
            detector=DetectorConfig(),
            remediation=RemediationConfig.from_yaml(remediation_config_path),
            log_level=log_level,
            dry_run=dry_run,
        )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """
    Get the application configuration (singleton).

    Returns:
        AppConfig: The application configuration

    Raises:
        ConfigurationError: If configuration is invalid
    """
    return AppConfig.load()


def setup_logging(level: Optional[str] = None) -> None:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               If None, uses config or defaults to INFO
    """
    if level is None:
        level = os.getenv('LOG_LEVEL', 'INFO').upper()

    numeric_level = getattr(logging, level, logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger.info(f"Logging configured at {level} level")


def validate_environment() -> Dict[str, bool]:
    """
    Validate that all required environment variables are set.

    Returns:
        Dict mapping variable names to their presence status
    """
    required_vars = [
        'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
        'AWS_REGION', 'AWS_ACCOUNT_ID'
    ]

    optional_vars = [
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
        'METABASE_URL', 'LOG_LEVEL', 'DRY_RUN'
    ]

    status = {}

    for var in required_vars:
        present = bool(os.getenv(var))
        status[var] = present
        if not present:
            logger.error(f"Required environment variable {var} is not set")

    for var in optional_vars:
        status[var] = bool(os.getenv(var))

    return status
