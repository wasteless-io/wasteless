"""
moto-backed test of the EC2 remediation *write* path — the riskiest code in
the project (it stops/starts real instances) and the one that otherwise only
runs against a live AWS account and skips in CI.

moto (`@mock_aws`) patches boto3 at the botocore layer, so `get_client()` in
core.aws_clients returns a mock-backed client without any real credentials.
This lets every PR exercise describe/start/stop against a simulated EC2 and
assert the state transitions actually happen — no cloud account required.

Needs a Postgres (the remediator writes to actions_log): runs in the CI
`backend` job, which provisions one. Skips cleanly if no DB is reachable so a
bare `pytest` locally doesn't error.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is a runtime dep, but stay defensive
    pass

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from core import aws_clients  # noqa: E402
from core.database import get_db_connection  # noqa: E402

REGION = "eu-west-1"


def _db_reachable() -> bool:
    try:
        conn = get_db_connection()
        conn.close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable (provided by CI backend job)"
)


@pytest.fixture
def moto_ec2(monkeypatch):
    """Activate moto, wire fake creds, and hand back a running instance id.

    A fake AWS_WRITE_ROLE_ARN is set so get_client(write=True) goes through the
    production AssumeRole path (which moto mocks deterministically): the session
    is cached under the role ARN and moto pins the account to the one in the
    ARN. Both the fixture and the remediator resolve to that same account, so
    they share one moto backend. Without a role, the default credential chain
    resolves nondeterministically under moto and the remediator ends up on a
    different account than the fixture — unable to see the instance it created.

    The test instance is created through the *same* get_client() factory the
    remediator uses (not a plain boto3.client), reinforcing the shared session.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.delenv("AWS_ROLE_ARN", raising=False)
    monkeypatch.setenv(
        "AWS_WRITE_ROLE_ARN", "arn:aws:iam::123456789012:role/wasteless-remediation-test"
    )

    with mock_aws():
        aws_clients.reset_cache()
        ec2 = aws_clients.get_client("ec2", region=REGION, write=True)
        # Amazon Linux-ish AMI id accepted by moto
        reservation = ec2.run_instances(
            ImageId="ami-12345678",
            InstanceType="t3.micro",
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": "wasteless-moto-test"}],
                }
            ],
        )
        instance_id = reservation["Instances"][0]["InstanceId"]
        yield ec2, instance_id
        aws_clients.reset_cache()


@requires_db
def test_get_instance_details_reads_moto_instance(moto_ec2):
    from remediators.ec2_remediator import EC2Remediator

    ec2, instance_id = moto_ec2
    remediator = EC2Remediator(dry_run=True)

    details = remediator.get_instance_details(instance_id)

    assert details is not None
    assert details["instance_id"] == instance_id
    assert details["instance_type"] == "t3.micro"
    assert details["state"] == "running"
    assert details["tags"]["Name"] == "wasteless-moto-test"


@requires_db
def test_get_instance_details_returns_none_for_unknown_instance(moto_ec2):
    from remediators.ec2_remediator import EC2Remediator

    remediator = EC2Remediator(dry_run=True)
    assert remediator.get_instance_details("i-doesnotexist0") is None


@requires_db
def test_start_instance_transitions_stopped_to_running(moto_ec2):
    """The real write path: a stopped instance is actually started and moto
    reflects the running state afterwards, and the action is logged to DB."""
    from remediators.ec2_remediator import EC2Remediator

    ec2, instance_id = moto_ec2
    ec2.stop_instances(InstanceIds=[instance_id])
    assert (
        ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0][
            "State"
        ]["Name"]
        == "stopped"
    )

    remediator = EC2Remediator(dry_run=False)
    result = remediator.start_instance(instance_id, reason="moto test rollback")

    assert result["success"] is True
    assert result.get("action_log_id") is not None
    # moto now reports the instance as running — the write actually happened
    state = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0][
        "State"
    ]["Name"]
    assert state == "running"


@requires_db
def test_start_instance_refuses_already_running(moto_ec2):
    """Guard: start_instance only acts on a stopped instance."""
    from remediators.ec2_remediator import EC2Remediator

    ec2, instance_id = moto_ec2  # instance is running
    remediator = EC2Remediator(dry_run=False)
    result = remediator.start_instance(instance_id)

    assert result["success"] is False
    assert "cannot start" in (result["error"] or "").lower()


@requires_db
def test_dry_run_does_not_mutate_instance(moto_ec2):
    """dry_run=True logs intent but must not change the instance state."""
    from remediators.ec2_remediator import EC2Remediator

    ec2, instance_id = moto_ec2
    ec2.stop_instances(InstanceIds=[instance_id])

    remediator = EC2Remediator(dry_run=True)
    result = remediator.start_instance(instance_id)

    assert result["success"] is True
    state = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0][
        "State"
    ]["Name"]
    assert state == "stopped"
