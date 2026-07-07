# src/core/finops_invariants/

Arithmetic/business guard-rails on every number that reaches a human
(dashboard, reports, marketing claims). Was a single 969-line
`finops_invariants.py`; split into thematic modules once it grew to ~60
functions with no internal organization beyond comment dividers. **Every
public name is still re-exported from `__init__.py` unchanged** — existing
code doing `from core.finops_invariants import X` needs no changes.

| Module | Covers |
|---|---|
| `_shared.py` | Constants and base types used across modules: `DESTRUCTIVE_ACTIONS`, `SERVICE_INTERRUPTING_ACTIONS`, `RISK_LEVELS`, `MIN_OBSERVATION_DAYS`, `FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS`, `FinOpsInvariantError`, `Violation`. Not meant to be imported directly from outside the package — go through `__init__.py`. |
| `arithmetic.py` | Foundational math: `annualize`, `waste_percentage`, `budget_used_percentage`, `validate_forecast`, `validate_service_breakdown`. Imported by several other modules. |
| `savings.py` | Caps and deduplication: `validate_recommendation_saving`, `validate_potential_vs_detected`, `deduplicated_total_savings`, `validate_realized_savings`. |
| `risk.py` | Risk-level floors and confidence scoring: `minimum_risk_for`, `validate_risk_level`, `assess_confidence`. |
| `observation.py` | Minimum observation windows before calling a resource idle, and the batch-workload exception: `minimum_observation_days`, `validate_observation_window`, `validate_batch_workload_classification`. |
| `cost_validation.py` | AWS pricing realism (EC2/EBS/EIP/NAT/RDS cost checks) and per-saving-type validation (resize/schedule/delete), plus required pricing metadata (`validate_pricing_metadata_complete`). |
| `dangerous_recommendations.py` | Recommendations that are detected but unsafe to act on as-is: `validate_underutilization_action`, `validate_ebs_delete`, `validate_nat_gateway_delete`, `validate_cloudwatch_retention_saving`, `validate_eks_resize`. |
| `forecast.py` | `linear_forecast`, `forecast_after_remediation`, `flags_budget_overrun`. |
| `claims.py` | CTO-safe wording for any public claim: `validate_claim_wording`, `validate_annualized_claim`, `validate_up_to_claim`, `validate_low_risk_claim`, `generate_cto_safe_summary`, `validate_dashboard_headline`. See `feedback-cto-safe-formulation` conventions. |
| `remediation_safety.py` | Approval → execution → audit-trail consistency: `validate_approval_required`, `validate_completed_status_requires_audit_log`, `validate_slack_approval_trace`, `validate_terraform_pr_remediation`, etc. |
| `audit.py` | Top-level orchestration: `audit_dataset()` runs the checks above over a full dataset and collects `Violation`s; `validate_read_only_audit` / `validate_resources_exist_in_aws` additionally cross-check claims against a real AWS account. |

## Dependency direction

`arithmetic.py`, `savings.py`, `risk.py`, `observation.py` and
`cost_validation.py` are leaves — nothing here imports from the rest of the
package. `forecast.py` and `claims.py` import from `arithmetic.py`; `audit.py`
imports from `arithmetic.py`, `savings.py` and `risk.py`. No submodule
imports from `audit.py` — keep it that way to avoid a circular import.

## Adding a new invariant

Put it in the module matching its theme, add it to the `from .<module>
import (...)` block and `__all__` in `__init__.py`, and add a test in
`tests/unit/test_finops_invariants.py` (or the more specific
`test_claim_safety.py` / `test_pricing_sanity.py` / `test_forecast.py` /
`test_recommendation_risk.py` — see `tests/unit/README.md`). Extend
`tests/fixtures/valid_finops_dataset.json` / `invalid_finops_dataset.json`
with a case exercising it if `audit_dataset()` should cover it too.
