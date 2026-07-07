# onboarding/

Everything needed to create the two least-privilege IAM roles wasteless
assumes in a target AWS account — `wasteless-readonly` (detection) and the
optional `wasteless-remediation` (approved write actions only). Explained
action-by-action in [docs/AWS_SETUP.md](../docs/AWS_SETUP.md).

| Path | Purpose |
|---|---|
| [`policies/`](policies/) | The IAM policy documents themselves (JSON) — the single source of truth for permissions. |
| [`terraform/`](terraform/) | Terraform module wrapping `policies/` into the two roles. Has its own README. |
| [`cloudformation/`](cloudformation/) | CloudFormation equivalent of the Terraform module, for accounts that don't use Terraform. |

Pick one onboarding path (Terraform or CloudFormation) — both create the
same two roles from the same underlying policies and produce the same
`AWS_ROLE_ARN` / `AWS_WRITE_ROLE_ARN` / `AWS_EXTERNAL_ID` values for `.env`.
`tests/unit/test_onboarding_policies.py` guards that the JSON policies stay
consistent with what both templates reference.
