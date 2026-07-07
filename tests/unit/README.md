# tests/unit/

Fast, no external dependencies — mocked AWS clients, mocked or in-memory DB.
This is where most of the FinOps correctness logic is pinned down; several
files are explicitly labeled as one "layer" of a broader audit strategy
(layer 2 = arithmetic/wording correctness, layer 3 = remediation-workflow
safety) — grep for "couche" in a file's docstring to see which layer it
belongs to.

| File | Covers |
|---|---|
| `test_validation.py` | Parameter validation functions shared across detectors. |
| `test_config.py` | `src/core/config.py` — `RemediationConfig.from_yaml()` and friends. |
| `test_safeguards.py` | `src/core/safeguards.py` — the 7-check gate before any AWS write action. |
| `test_aws_clients.py` | `src/core/aws_clients.py` — the central boto3 client factory and AssumeRole handling. |
| `test_snapshots.py` | `src/core/snapshots.py` — daily waste snapshot helper. |
| `test_llm.py` | `src/core/llm.py` — AI insights degrade silently (no crash) when no provider is configured. |
| `test_finops_invariants.py` | `src/core/finops_invariants.py` — the arithmetic/business guard-rails module itself. |
| `test_confidence_score.py` | Deterministic confidence scoring (audit layer 2). |
| `test_pricing_sanity.py` | AWS pricing realism checks (audit layer 2). |
| `test_forecast.py` | Advanced forecasting math (audit layer 2). |
| `test_claim_safety.py` | CTO-safe wording / claim consistency (audit layer 2) — see `feedback-cto-safe-formulation` conventions. |
| `test_recommendation_risk.py` | Environment criticality scoring and dangerous-recommendation detection. |
| `test_remediation_workflow.py` | End-to-end safety of the remediation workflow (audit layer 3). |
| `test_resource_remediators.py` | `src/remediators/resource_remediator.py` — gp2 migration, NAT gateway, load balancer, volume-delete remediators. |
| `test_terraform_editor.py` | `src/remediators/terraform_editor.py` — HCL block removal/edit, reference detection, `terraform validate`. |
| `test_terraform_mapper.py` | `src/remediators/terraform_mapper.py` — AWS ID → Terraform address/HCL block resolution. |
| `test_terraform_pr.py` | `src/remediators/terraform_pr.py` — PR orchestration, risk-level routing, PR template. |
| `test_steampipe_collector.py` | `src/collectors/steampipe.py` wrapper + Steampipe-backed collectors. |
| `test_steampipe_detectors.py` | The Steampipe-native detectors (NAT gateway, gp2 migration, ELB, VPC, and the `*_steampipe.py` variants) — currently the only place these run, since none are wired into `wasteless.sh collect` yet. |
| `test_weekly_digest.py` | `src/reports/weekly_digest.py` — activity report over a date range. |
| `test_onboarding_policies.py` | Guards that `onboarding/policies/*.json` stays the source of truth both the Terraform module and the CloudFormation template reference. |
