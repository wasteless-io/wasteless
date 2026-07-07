"""
End-to-end safety of the approval → execution → audit trail: approval requirements, audit-log presence, status/timestamp consistency, Slack/Terraform-PR traceability.
"""

from typing import Optional
from ._shared import DESTRUCTIVE_ACTIONS, FinOpsInvariantError

REMEDIATION_STATES = (
    "detected",
    "validated",
    "approval_required",
    "approved",
    "scheduled",
    "executed",
    "rolled_back",
    "ignored",
    "expired",
)


def validate_approval_required(
    action: str, environment: Optional[str], risk: str, approval_required: bool
) -> bool:
    """Une action destructive, en production, ou high/critical risk doit
    exiger une approbation explicite — jamais d'exécution silencieuse."""
    action_key = (action or "").lower()
    env = (environment or "unknown").lower()
    is_destructive = action_key in DESTRUCTIVE_ACTIONS
    is_prod_like = env in ("production", "prod", "unknown")
    needs_approval = is_destructive or is_prod_like or risk in ("high", "critical")

    if needs_approval and not approval_required:
        raise FinOpsInvariantError(
            f"Action '{action}' on environment '{environment}' (risk="
            f"{risk!r}) requires approval_required=True"
        )
    return approval_required


def validate_auto_execution(
    execution_mode: str, owner: Optional[str], rollback_plan: Optional[str]
) -> bool:
    """Une exécution automatique exige un owner à prévenir et un plan de
    rollback documenté — sans les deux, elle doit rester manuelle."""
    if execution_mode == "auto":
        if not owner:
            raise FinOpsInvariantError("Auto execution requires a known owner")
        if not rollback_plan:
            raise FinOpsInvariantError("Auto execution requires a documented rollback plan")
    return True


def validate_completed_status_requires_audit_log(status: str, audit_log_id: Optional[str]) -> bool:
    if status == "executed" and not audit_log_id:
        raise FinOpsInvariantError("Status 'executed' requires an audit_log_id")
    return True


def validate_realized_savings_status(status: str, realized_monthly_saving: float) -> float:
    """Une économie réalisée non nulle exige le statut 'executed' — les
    autres états (approved, scheduled, ignored...) ne comptent pas."""
    if realized_monthly_saving and realized_monthly_saving > 0 and status != "executed":
        raise FinOpsInvariantError(
            f"Realized saving ({realized_monthly_saving}) reported with "
            f"status '{status}', expected 'executed'"
        )
    return realized_monthly_saving


def validate_no_saving_when_ignored_or_expired(status: str, saving: float) -> bool:
    if status in ("ignored", "expired") and saving and saving > 0:
        raise FinOpsInvariantError(
            f"Status '{status}' recommendation cannot count {saving} as a " f"saving"
        )
    return True


def validate_approval_identity(approval_required: bool, approved_by: Optional[str]) -> bool:
    if approval_required and not approved_by:
        raise FinOpsInvariantError("approval_required=True but no approved_by identity recorded")
    return True


def validate_execution_timestamp(status: str, executed_at: Optional[str]) -> bool:
    if status == "executed" and not executed_at:
        raise FinOpsInvariantError("Status 'executed' requires an executed_at timestamp")
    return True


def validate_slack_approval_trace(approval_channel: Optional[str], trace_id: Optional[str]) -> bool:
    if approval_channel == "slack" and not trace_id:
        raise FinOpsInvariantError("Slack approval requires a trace_id to stay auditable")
    return True


def validate_terraform_pr_remediation(execution_mode: str, pull_request_url: Optional[str]) -> bool:
    if execution_mode == "terraform_pr" and not pull_request_url:
        raise FinOpsInvariantError("execution_mode 'terraform_pr' requires a pull_request_url")
    return True
