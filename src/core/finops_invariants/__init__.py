"""
Invariants FinOps — garde-fous arithmétiques et métier sur les chiffres.

Tout montant destiné à être affiché (dashboard, rapports, claims marketing)
doit passer ces invariants avant publication : un chiffre qu'un CTO peut
démonter en une division décrédibilise tout le produit. Les règles couvrent
les erreurs relevées par l'audit de cohérence : annualisation fausse,
pourcentages impossibles, double comptage d'une ressource, forecast sous le
réalisé, économies « réalisées » sans action exécutée, delete en production
classé low risk, montants sans devise ni période.

Fonctions pures, sans accès base : les appelants (détecteurs, UI, rapports)
fournissent les chiffres et décident quoi faire d'une violation.

This used to be a single 969-line file; it's now a package split by theme
(one file per section below) so each concern can be read independently.
Every name below is re-exported here unchanged — existing callers using
`from core.finops_invariants import X` do not need to change anything.
"""

from ._shared import (
    MONTHS_PER_YEAR,
    HOURS_PER_MONTH,
    DESTRUCTIVE_ACTIONS,
    SERVICE_INTERRUPTING_ACTIONS,
    RISK_LEVELS,
    LOW_CAP_FIELDS,
    MEDIUM_CAP_FIELDS,
    MIN_OBSERVATION_DAYS,
    DEFAULT_MIN_OBSERVATION_DAYS,
    FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS,
    FinOpsInvariantError,
    Violation,
)
from .arithmetic import (
    annualize,
    waste_percentage,
    budget_used_percentage,
    validate_forecast,
    validate_service_breakdown,
)
from .savings import (
    validate_recommendation_saving,
    validate_potential_vs_detected,
    deduplicated_total_savings,
    validate_realized_savings,
)
from .risk import (
    minimum_risk_for,
    validate_risk_level,
    assess_confidence,
)
from .observation import (
    minimum_observation_days,
    validate_observation_window,
    validate_batch_workload_classification,
)
from .cost_validation import (
    validate_cost_within_tolerance,
    validate_ec2_cost,
    validate_ebs_cost,
    validate_elastic_ip_cost,
    validate_nat_gateway_cost,
    validate_rds_cost,
    validate_pricing_metadata_complete,
    validate_estimated_cost_matches_unit_price,
    validate_resize_saving,
    validate_schedule_saving,
    validate_delete_saving,
)
from .dangerous_recommendations import (
    validate_underutilization_action,
    validate_ebs_delete,
    validate_nat_gateway_delete,
    validate_cloudwatch_retention_saving,
    validate_eks_resize,
)
from .forecast import (
    linear_forecast,
    forecast_after_remediation,
    flags_budget_overrun,
)
from .claims import (
    validate_claim_percentage,
    validate_claim_wording,
    validate_annualized_claim,
    validate_up_to_claim,
    validate_low_risk_claim,
    generate_cto_safe_summary,
    validate_dashboard_headline,
    validate_annualized_claim_assumptions,
)
from .remediation_safety import (
    validate_approval_required,
    validate_auto_execution,
    validate_completed_status_requires_audit_log,
    validate_realized_savings_status,
    validate_no_saving_when_ignored_or_expired,
    validate_approval_identity,
    validate_execution_timestamp,
    validate_slack_approval_trace,
    validate_terraform_pr_remediation,
)
from .audit import (
    audit_dataset,
    validate_within_tolerance_pct,
    validate_resources_exist_in_aws,
    validate_read_only_audit,
    validate_audit_trace,
)

__all__ = [
    "DEFAULT_MIN_OBSERVATION_DAYS",
    "DESTRUCTIVE_ACTIONS",
    "FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS",
    "FinOpsInvariantError",
    "HOURS_PER_MONTH",
    "LOW_CAP_FIELDS",
    "MEDIUM_CAP_FIELDS",
    "MIN_OBSERVATION_DAYS",
    "MONTHS_PER_YEAR",
    "RISK_LEVELS",
    "SERVICE_INTERRUPTING_ACTIONS",
    "Violation",
    "annualize",
    "assess_confidence",
    "audit_dataset",
    "budget_used_percentage",
    "deduplicated_total_savings",
    "flags_budget_overrun",
    "forecast_after_remediation",
    "generate_cto_safe_summary",
    "linear_forecast",
    "minimum_observation_days",
    "minimum_risk_for",
    "validate_annualized_claim",
    "validate_annualized_claim_assumptions",
    "validate_approval_identity",
    "validate_approval_required",
    "validate_audit_trace",
    "validate_auto_execution",
    "validate_batch_workload_classification",
    "validate_claim_percentage",
    "validate_claim_wording",
    "validate_cloudwatch_retention_saving",
    "validate_completed_status_requires_audit_log",
    "validate_cost_within_tolerance",
    "validate_dashboard_headline",
    "validate_delete_saving",
    "validate_ebs_cost",
    "validate_ebs_delete",
    "validate_ec2_cost",
    "validate_eks_resize",
    "validate_elastic_ip_cost",
    "validate_estimated_cost_matches_unit_price",
    "validate_execution_timestamp",
    "validate_forecast",
    "validate_low_risk_claim",
    "validate_nat_gateway_cost",
    "validate_nat_gateway_delete",
    "validate_no_saving_when_ignored_or_expired",
    "validate_observation_window",
    "validate_potential_vs_detected",
    "validate_pricing_metadata_complete",
    "validate_rds_cost",
    "validate_read_only_audit",
    "validate_realized_savings",
    "validate_realized_savings_status",
    "validate_recommendation_saving",
    "validate_resize_saving",
    "validate_resources_exist_in_aws",
    "validate_risk_level",
    "validate_schedule_saving",
    "validate_service_breakdown",
    "validate_slack_approval_trace",
    "validate_terraform_pr_remediation",
    "validate_underutilization_action",
    "validate_up_to_claim",
    "validate_within_tolerance_pct",
    "waste_percentage",
]
