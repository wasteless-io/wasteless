#!/usr/bin/env python3
"""
Terraform PR Remediator for Wasteless

GitOps remediation flow: instead of calling the AWS API, propose the change
as a pull request on the Terraform repo that declares the resource:

    clone repo → map AWS ID to its HCL block (terraform_mapper)
    → remove/edit the block (terraform_editor) → terraform validate
    → branch + commit + `gh pr create`

The PR body carries the estimated savings, a risk level derived from the
detection confidence, the rollback plan and the Cost Explorer evidence.

Routing (who goes through a PR vs the direct API path) is decided by
TerraformPRConfig.requires_pr(). Returns None when the resource is not
managed by Terraform — callers must fall back to the API remediators.
Authentication relies on an already-authenticated `gh` CLI; no token is
stored in Wasteless.

Author: Wasteless
"""

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional


from remediators.terraform_editor import (
    find_references,
    remove_block,
    validate_directory,
)
from remediators.terraform_mapper import TerraformMapper, TerraformResource

logger = logging.getLogger(__name__)


class TerraformPRError(Exception):
    """Raised when the PR flow cannot proceed safely (caller may fall back)."""

    pass


@dataclass
class PRProposal:
    """A generated (and, unless dry-run, opened) remediation PR."""

    resource_id: str
    address: str
    action: str  # 'remove' or 'edit'
    branch: str
    title: str
    body: str
    diff: str
    dry_run: bool
    pr_url: Optional[str] = None


def risk_level(confidence: float) -> str:
    """Map detection confidence to the risk label shown in the PR."""
    if confidence >= 0.90:
        return "low"
    if confidence >= 0.80:
        return "medium"
    return "high"


class TerraformPRRemediator:
    """Open remediation PRs on the Terraform repo declaring the resources."""

    def __init__(self, config, dry_run: bool = True):
        """
        Args:
            config: TerraformPRConfig (repo, base_branch, terraform_dir...)
            dry_run: when True, generate branch/title/body/diff but do not
                     push anything nor call gh.
        """
        if not config.repo:
            raise TerraformPRError("terraform_pr.repo is not configured")
        self.config = config
        self.dry_run = dry_run

    def propose_removal(
        self,
        resource_id: str,
        resource_label: str,
        monthly_savings_eur: float,
        confidence: float,
        reason: str = "",
        cost_evidence: Optional[Dict] = None,
        workdir: Optional[str] = None,
        mapper: Optional[TerraformMapper] = None,
    ) -> Optional[PRProposal]:
        """
        Propose the removal of a Terraform-managed resource via a PR.

        Args:
            resource_id: live AWS ID (eipalloc-xxx, nat-xxx...)
            resource_label: human label for the PR ("orphan EIP"...)
            monthly_savings_eur / confidence / reason: from the recommendation
            cost_evidence: optional Cost Explorer datapoints {label: value}
            workdir: existing checkout to use instead of cloning (tests, e2e)
            mapper: pre-built TerraformMapper (skips `terraform show`)

        Returns None when the resource is not managed by this Terraform state
        (fall back to the API remediation path).
        """
        workdir = workdir or self._clone()
        tf_dir = f"{workdir}/{self.config.terraform_dir}".rstrip("/.")

        mapper = mapper or TerraformMapper.from_terraform_dir(tf_dir)
        resource = mapper.locate(resource_id, tf_dir)
        if resource is None:
            return None
        if not resource.located:
            # Child module or drifted block: not editable in v1
            logger.info(f"{resource.address}: block not editable, falling back")
            return None

        references = find_references(
            tf_dir,
            resource.resource_type,
            resource.name,
            exclude_file=resource.file,
            exclude_range=(resource.start_line, resource.end_line),
        )
        if references:
            where = ", ".join(f"{f}:{line}" for f, line in references)
            raise TerraformPRError(
                f"Removing {resource.address} would leave dangling "
                f"references ({where}) — needs a human"
            )

        edit = remove_block(tf_dir, resource.file, resource.start_line, resource.end_line)

        ok, message = validate_directory(tf_dir)
        if not ok:
            raise TerraformPRError(
                f"terraform validate failed after removing " f"{resource.address}: {message}"
            )

        branch = f"wasteless/remove-{resource_id}"
        title = (
            f"chore(wasteless): remove {resource_label} "
            f"{resource_id} (~{monthly_savings_eur:.0f} €/mo)"
        )
        body = self._pr_body(
            resource,
            "remove",
            resource_label,
            monthly_savings_eur,
            confidence,
            reason,
            cost_evidence,
        )

        proposal = PRProposal(
            resource_id=resource_id,
            address=resource.address,
            action="remove",
            branch=branch,
            title=title,
            body=body,
            diff=edit.unified_diff,
            dry_run=self.dry_run,
        )
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would open PR '{title}' on {self.config.repo}")
        else:
            proposal.pr_url = self._open_pr(workdir, proposal)
        return proposal

    def _clone(self) -> str:
        """Shallow-clone the configured repo and init Terraform (providers
        for validate, backend for `terraform show`)."""
        workdir = tempfile.mkdtemp(prefix="wasteless-tf-")
        self._run(
            ["gh", "repo", "clone", self.config.repo, workdir, "--", "--depth", "1"], cwd=None
        )
        tf_dir = f"{workdir}/{self.config.terraform_dir}".rstrip("/.")
        self._run(["terraform", "init", "-input=false"], cwd=tf_dir)
        return workdir

    def _open_pr(self, workdir: str, proposal: PRProposal) -> str:
        """Branch, commit the edit and open the PR. Returns the PR URL."""
        self._run(["git", "checkout", "-b", proposal.branch], cwd=workdir)
        self._run(["git", "add", "-A"], cwd=workdir)
        self._run(["git", "commit", "-m", proposal.title], cwd=workdir)
        self._run(["git", "push", "-u", "origin", proposal.branch], cwd=workdir)
        result = self._run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                self.config.repo,
                "--base",
                self.config.base_branch,
                "--head",
                proposal.branch,
                "--title",
                proposal.title,
                "--body",
                proposal.body,
            ],
            cwd=workdir,
        )
        pr_url = result.stdout.strip().splitlines()[-1]
        logger.info(f"Opened PR {pr_url}")
        return pr_url

    def _pr_body(
        self,
        resource: TerraformResource,
        action: str,
        resource_label: str,
        monthly_savings_eur: float,
        confidence: float,
        reason: str,
        cost_evidence: Optional[Dict],
    ) -> str:
        risk = risk_level(confidence)
        lines = [
            f"Wasteless detected a wasteful resource and proposes to "
            f"{action} it from the Terraform code.",
            "",
            "## Summary",
            "",
            "| | |",
            "|---|---|",
            f"| Resource | `{resource.resource_id}` ({resource_label}) |",
            f"| Terraform address | `{resource.address}` |",
            f"| File | `{resource.file}` (lines {resource.start_line}-{resource.end_line}) |",
            f"| Estimated savings | **{monthly_savings_eur:.2f} €/month** "
            f"(~{monthly_savings_eur * 12:.0f} €/year) |",
            f"| Risk | **{risk}** (detection confidence {confidence:.0%}) |",
        ]
        if reason:
            lines.append(f"| Why | {reason} |")

        lines += [
            "",
            "## Cost Explorer evidence",
            "",
        ]
        if cost_evidence:
            lines += ["| | |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in cost_evidence.items()]
        else:
            lines.append(
                "_No Cost Explorer history attached. Actual savings will be "
                "verified by Wasteless after merge and reported on the "
                "Savings Realized dashboard._"
            )

        lines += [
            "",
            "## Rollback plan",
            "",
            "1. Revert this PR (`git revert`) and apply — Terraform recreates "
            "the resource from code.",
            "2. Wasteless keeps a state snapshot of the resource "
            "(`rollback_snapshots`) for the configured retention period.",
            "",
            "---",
            "_Opened automatically by [Wasteless](https://github.com/wasteless-io). "
            "Merging applies the change through your usual Terraform pipeline; "
            "closing marks the recommendation as rejected._",
        ]
        return "\n".join(lines)

    @staticmethod
    def _run(cmd: List[str], cwd: Optional[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise TerraformPRError(f"{' '.join(cmd[:3])} failed: {result.stderr.strip()}")
        return result
