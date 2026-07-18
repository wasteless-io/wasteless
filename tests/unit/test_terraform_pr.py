"""
Unit tests for the Terraform PR remediator (orchestration, PR template,
dry-run behaviour). git/gh and terraform validate are mocked.
"""

from unittest.mock import patch, MagicMock

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.config import TerraformPRConfig
from remediators.terraform_mapper import TerraformMapper
from remediators.terraform_pr import (
    TerraformPRError,
    TerraformPRRemediator,
    risk_level,
)

STATE = {
    "values": {
        "root_module": {
            "resources": [
                {
                    "address": "aws_eip.orphan",
                    "mode": "managed",
                    "type": "aws_eip",
                    "name": "orphan",
                    "values": {"id": "eipalloc-0aaa111"},
                },
                {
                    "address": "aws_eip.nat",
                    "mode": "managed",
                    "type": "aws_eip",
                    "name": "nat",
                    "values": {"id": "eipalloc-0bbb222"},
                },
            ],
        }
    },
}

WASTE_TF = """resource "aws_eip" "orphan" {
  domain = "vpc"
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "unused" {
  allocation_id = aws_eip.nat.id
  subnet_id     = "subnet-123"
}
"""


@pytest.fixture
def tf_dir(tmp_path):
    (tmp_path / "waste.tf").write_text(WASTE_TF)
    return str(tmp_path)


@pytest.fixture
def remediator():
    config = TerraformPRConfig(
        enabled=True, repo="acme/infra", base_branch="main", terraform_dir="."
    )
    return TerraformPRRemediator(config, dry_run=True)


@pytest.fixture(autouse=True)
def valid_validate():
    with patch(
        "remediators.terraform_pr.validate_directory", return_value=(True, "Success!")
    ) as mock:
        yield mock


class TestRiskLevel:

    def test_thresholds(self):
        assert risk_level(0.95) == "low"
        assert risk_level(0.85) == "medium"
        assert risk_level(0.70) == "high"


class TestConfigGuard:

    def test_missing_repo_raises(self):
        with pytest.raises(TerraformPRError):
            TerraformPRRemediator(TerraformPRConfig(enabled=True, repo=""))


class TestProposeRemoval:

    def test_dry_run_builds_proposal_without_gh(self, remediator, tf_dir):
        with patch.object(TerraformPRRemediator, "_run") as run_mock:
            proposal = remediator.propose_removal(
                "eipalloc-0aaa111",
                "orphan EIP",
                monthly_savings_eur=3.65,
                confidence=0.92,
                reason="unattached for 21 days",
                workdir=tf_dir,
                mapper=TerraformMapper(STATE),
            )
        run_mock.assert_not_called()
        assert proposal.dry_run
        assert proposal.pr_url is None
        assert proposal.branch == "wasteless/remove-eipalloc-0aaa111"
        assert proposal.address == "aws_eip.orphan"

    def test_pr_body_contains_the_four_pillars(self, remediator, tf_dir):
        proposal = remediator.propose_removal(
            "eipalloc-0aaa111",
            "orphan EIP",
            monthly_savings_eur=3.65,
            confidence=0.92,
            reason="unattached for 21 days",
            cost_evidence={"Cost last 30 days": "$3.65"},
            workdir=tf_dir,
            mapper=TerraformMapper(STATE),
        )
        assert "$3.65/month" in proposal.body  # savings estimate
        assert "**low** (detection confidence 92%)" in proposal.body  # risk
        assert "## Rollback plan" in proposal.body  # rollback
        assert "Cost last 30 days | $3.65" in proposal.body  # CE proof
        assert "unattached for 21 days" in proposal.body

    def test_block_is_removed_and_diffed(self, remediator, tf_dir):
        proposal = remediator.propose_removal(
            "eipalloc-0aaa111",
            "orphan EIP",
            monthly_savings_eur=3.65,
            confidence=0.92,
            workdir=tf_dir,
            mapper=TerraformMapper(STATE),
        )
        content = open(f"{tf_dir}/waste.tf").read()
        assert '"orphan"' not in content
        assert '-resource "aws_eip" "orphan" {' in proposal.diff

    def test_unmanaged_resource_returns_none(self, remediator, tf_dir):
        proposal = remediator.propose_removal(
            "eipalloc-unknown",
            "orphan EIP",
            monthly_savings_eur=3.65,
            confidence=0.92,
            workdir=tf_dir,
            mapper=TerraformMapper(STATE),
        )
        assert proposal is None

    def test_dangling_reference_aborts(self, remediator, tf_dir):
        # aws_eip.nat is referenced by the NAT gateway block
        with pytest.raises(TerraformPRError, match="dangling"):
            remediator.propose_removal(
                "eipalloc-0bbb222",
                "EIP",
                monthly_savings_eur=3.65,
                confidence=0.92,
                workdir=tf_dir,
                mapper=TerraformMapper(STATE),
            )

    def test_failed_validate_aborts(self, remediator, tf_dir, valid_validate):
        valid_validate.return_value = (False, "reference to undeclared resource")
        with pytest.raises(TerraformPRError, match="validate failed"):
            remediator.propose_removal(
                "eipalloc-0aaa111",
                "orphan EIP",
                monthly_savings_eur=3.65,
                confidence=0.92,
                workdir=tf_dir,
                mapper=TerraformMapper(STATE),
            )

    def test_real_run_opens_pr(self, tf_dir):
        config = TerraformPRConfig(enabled=True, repo="acme/infra")
        remediator = TerraformPRRemediator(config, dry_run=False)
        pr_result = MagicMock(stdout="https://github.com/acme/infra/pull/7\n")
        with patch.object(TerraformPRRemediator, "_run", return_value=pr_result) as run_mock:
            proposal = remediator.propose_removal(
                "eipalloc-0aaa111",
                "orphan EIP",
                monthly_savings_eur=3.65,
                confidence=0.92,
                workdir=tf_dir,
                mapper=TerraformMapper(STATE),
            )
        assert proposal.pr_url == "https://github.com/acme/infra/pull/7"
        commands = [call.args[0][:3] for call in run_mock.call_args_list]
        assert ["git", "checkout", "-b"] in commands
        assert ["git", "push", "-u"] in commands
        assert ["gh", "pr", "create"] in commands
