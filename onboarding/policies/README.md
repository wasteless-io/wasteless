# onboarding/policies/

The IAM permission documents themselves — the single source of truth that
both `../terraform/` and `../cloudformation/` wrap into roles. Each action
is explained individually in [docs/AWS_SETUP.md](../../docs/AWS_SETUP.md);
this folder is where the actual JSON lives.

| File | Statement(s) | Scope |
|---|---|---|
| `readonly.json` | `WastelessReadOnly` (15 actions) | Describe/Get/List only — everything the collectors and detectors need to see resources, metrics and cost data. Never grants write access. |
| `remediation.json` | `Ec2Remediation` (8 actions), `TagRollbackSnapshots` (tags snapshots created for rollback with `ec2:CreateAction = CreateSnapshot`, scoped via a condition so it can't tag arbitrary snapshots), `ElbRemediation` (delete unused load balancers) | Only the write actions a human-approved recommendation may execute — attached to `wasteless-remediation`, never to `wasteless-readonly`. |

`tests/unit/test_onboarding_policies.py` guards that these stay the
authoritative set both templates reference — if you add a new remediation
action type in `src/remediators/` or `ui/utils/action_registry.py`, the
matching IAM action belongs here first, then in the Terraform/CloudFormation
templates.
