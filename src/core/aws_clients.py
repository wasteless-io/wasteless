"""
Central boto3 client factory with cross-account AssumeRole support.

All AWS clients in wasteless must be created through get_client() so that
authentication is handled in one place:

- If AWS_ROLE_ARN is set, read clients use STS AssumeRole on that role
  (the customer's `wasteless-readonly` role), with automatic credential
  refresh handled by botocore.
- Write clients (write=True) require AWS_WRITE_ROLE_ARN (the customer's
  `wasteless-remediation` role). If only the read role is configured,
  a write request fails closed with ConfigurationError instead of
  silently running with broader local credentials.
- If no role is configured, clients fall back to the default boto3
  credential chain (env keys, ~/.aws, instance profile) — the legacy
  IAM-user setup keeps working unchanged.

Assumed-role sessions are cached per role ARN and shared across threads;
botocore's DeferredRefreshableCredentials re-assumes the role before the
STS credentials expire, so long-running processes (UI scheduler) never
hold stale credentials.
"""

import threading
from typing import Callable, Optional

import boto3
import botocore.session
from botocore.credentials import (
    AssumeRoleCredentialFetcher,
    DeferredRefreshableCredentials,
)

from .config import AWSConfig, ConfigurationError

_lock = threading.Lock()
_sessions = {}

# Cache key for the default-chain session (role_arn is None)
_DEFAULT_KEY = "__default_chain__"


def reset_cache() -> None:
    """Drop all cached sessions (used by tests and config reloads)."""
    with _lock:
        _sessions.clear()


def _assumed_session(role_arn: str, external_id: Optional[str], session_name: str) -> boto3.Session:
    """Build a boto3 Session whose credentials come from sts:AssumeRole,
    auto-refreshed by botocore before expiry."""
    base = botocore.session.Session()
    source_credentials = base.get_credentials()
    if source_credentials is None:
        raise ConfigurationError(
            f"Cannot assume role {role_arn}: no source AWS credentials found "
            "(configure the default credential chain: env vars, ~/.aws or "
            "an instance profile)"
        )

    extra_args = {"RoleSessionName": session_name}
    if external_id:
        extra_args["ExternalId"] = external_id

    fetcher = AssumeRoleCredentialFetcher(
        client_creator=base.create_client,
        source_credentials=source_credentials,
        role_arn=role_arn,
        extra_args=extra_args,
    )

    botocore_sess = botocore.session.Session()
    botocore_sess._credentials = DeferredRefreshableCredentials(
        method="assume-role",
        refresh_using=fetcher.fetch_credentials,
    )
    return boto3.Session(botocore_session=botocore_sess)


def _build_session(
    role_arn: Optional[str], external_id: Optional[str], session_name: str
) -> boto3.Session:
    if role_arn is None:
        return boto3.Session()
    return _assumed_session(role_arn, external_id, session_name)


def _select_role_arn(config: AWSConfig, write: bool) -> Optional[str]:
    if write:
        if config.write_role_arn:
            return config.write_role_arn
        if config.role_arn:
            # Fail closed: never run a write action with the read-only role
            # or with whatever broader local credentials happen to be around.
            raise ConfigurationError(
                "Write action requested but AWS_WRITE_ROLE_ARN is not set. "
                "Set it to the wasteless-remediation role ARN, or unset "
                "AWS_ROLE_ARN to use the legacy credential chain."
            )
        return None
    return config.role_arn or None


def get_client(
    service: str,
    *,
    region: Optional[str] = None,
    write: bool = False,
    session_factory: Optional[Callable] = None,
    **client_kwargs,
):
    """
    Create a boto3 client for `service`.

    Args:
        service: AWS service name (e.g. 'ec2', 'ce', 'cloudwatch')
        region: explicit region; defaults to AWS_REGION (eu-west-1)
        write: True for remediation actions — selects the write role and
               fails closed if only the read role is configured
        session_factory: test hook — callable(role_arn, external_id,
               session_name) returning a session-like object
        **client_kwargs: passed through to session.client() (e.g. config=)

    Raises:
        ConfigurationError: write requested without AWS_WRITE_ROLE_ARN
            while AWS_ROLE_ARN is set, or no source credentials available
            to assume the configured role.
    """
    config = AWSConfig.from_env()
    role_arn = _select_role_arn(config, write)
    resolved_region = region or config.region
    builder = session_factory or _build_session

    key = role_arn or _DEFAULT_KEY
    with _lock:
        session = _sessions.get(key)
        if session is None:
            session = builder(role_arn, config.external_id, config.role_session_name)
            _sessions[key] = session

    return session.client(service, region_name=resolved_region, **client_kwargs)
