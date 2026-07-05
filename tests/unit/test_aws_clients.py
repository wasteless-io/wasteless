"""
Unit tests for the central AWS client factory (src/core/aws_clients.py).
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.aws_clients import get_client, reset_cache
from core.config import ConfigurationError

READ_ROLE = 'arn:aws:iam::123456789012:role/wasteless-readonly'
WRITE_ROLE = 'arn:aws:iam::123456789012:role/wasteless-remediation'

BASE_ENV = {
    'AWS_REGION': 'eu-west-1',
    'AWS_ACCOUNT_ID': '123456789012',
}


def make_factory():
    """Session factory recording calls, returning a mock session."""
    session = MagicMock()
    factory = MagicMock(return_value=session)
    return factory, session


@pytest.fixture(autouse=True)
def clean_cache():
    reset_cache()
    yield
    reset_cache()


def env(**extra):
    merged = dict(BASE_ENV, **extra)
    return patch.dict(os.environ, merged, clear=True)


class TestDefaultChainFallback:
    """Without role variables, behavior is the legacy credential chain."""

    def test_no_role_uses_default_chain(self):
        factory, session = make_factory()
        with env():
            get_client('ec2', session_factory=factory)
        factory.assert_called_once()
        role_arn = factory.call_args[0][0]
        assert role_arn is None

    def test_write_without_any_role_uses_default_chain(self):
        factory, session = make_factory()
        with env():
            get_client('ec2', write=True, session_factory=factory)
        assert factory.call_args[0][0] is None


class TestRoleSelection:
    def test_read_role_assumed_when_configured(self):
        factory, session = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE):
            get_client('ec2', session_factory=factory)
        assert factory.call_args[0][0] == READ_ROLE

    def test_write_role_selected_only_when_write(self):
        factory, session = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE, AWS_WRITE_ROLE_ARN=WRITE_ROLE):
            get_client('ec2', session_factory=factory)
            get_client('ec2', write=True, session_factory=factory)
        assert factory.call_args_list[0][0][0] == READ_ROLE
        assert factory.call_args_list[1][0][0] == WRITE_ROLE

    def test_write_fails_closed_with_read_role_only(self):
        factory, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE):
            with pytest.raises(ConfigurationError):
                get_client('ec2', write=True, session_factory=factory)
        factory.assert_not_called()

    def test_external_id_passed_iff_set(self):
        factory, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE, AWS_EXTERNAL_ID='secret-123'):
            get_client('ec2', session_factory=factory)
        assert factory.call_args[0][1] == 'secret-123'

        reset_cache()
        factory2, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE):
            get_client('ec2', session_factory=factory2)
        assert factory2.call_args[0][1] is None

    def test_session_name_default_and_override(self):
        factory, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE):
            get_client('ec2', session_factory=factory)
        assert factory.call_args[0][2] == 'wasteless'

        reset_cache()
        factory2, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE, AWS_ROLE_SESSION_NAME='custom'):
            get_client('ec2', session_factory=factory2)
        assert factory2.call_args[0][2] == 'custom'


class TestRegionPrecedence:
    def test_explicit_region_wins(self):
        factory, session = make_factory()
        with env():
            get_client('ec2', region='us-east-1', session_factory=factory)
        assert session.client.call_args[1]['region_name'] == 'us-east-1'

    def test_env_region_is_default(self):
        factory, session = make_factory()
        with env():
            get_client('ec2', session_factory=factory)
        assert session.client.call_args[1]['region_name'] == 'eu-west-1'

    def test_client_kwargs_passthrough(self):
        factory, session = make_factory()
        sentinel = object()
        with env():
            get_client('sts', config=sentinel, session_factory=factory)
        assert session.client.call_args[1]['config'] is sentinel


class TestSessionCache:
    def test_same_role_builds_one_session(self):
        factory, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE):
            get_client('ec2', session_factory=factory)
            get_client('cloudwatch', session_factory=factory)
        assert factory.call_count == 1

    def test_read_and_write_roles_are_separate_sessions(self):
        factory, _ = make_factory()
        with env(AWS_ROLE_ARN=READ_ROLE, AWS_WRITE_ROLE_ARN=WRITE_ROLE):
            get_client('ec2', session_factory=factory)
            get_client('ec2', write=True, session_factory=factory)
        assert factory.call_count == 2
