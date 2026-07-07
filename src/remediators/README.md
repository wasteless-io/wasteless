# src/remediators/

Executes approved recommendations against AWS (or against Terraform, for the
GitOps flow) — the only code in the repo allowed to make write API calls,
and only after `src/core/safeguards.py` has cleared them.

Two remediation modes, chosen per `recommendation_type` in
`ui/utils/action_registry.py`:

## Direct AWS execution (`boto3` / `remediator` execution mode)

| File | Purpose |
|---|---|
| `ec2_remediator.py` | `EC2Remediator` — stop / terminate an EC2 instance, with a rollback snapshot written before acting. |
| `resource_remediator.py` | `ResourceRemediator` base + `Gp2MigrationRemediator`, `VolumeDeleteRemediator`, `NATGatewayRemediator`, `LoadBalancerRemediator` — one subclass per non-EC2 resource type. |

## GitOps flow (infra managed as Terraform)

| File | Purpose |
|---|---|
| `terraform_pr.py` | `TerraformPRRemediator` — instead of calling the AWS API, opens a pull request that edits the resource's `.tf` file. `risk_level()` classifies the change for routing (see `docs/` for the criticality → review-path mapping); `PRProposal` is the diff handed to the PR. Orchestrated from `ui/utils/terraform_pr.py` (`terraform_pr_sync_job`, every 5 min). |
| `terraform_mapper.py` | `TerraformMapper` — resolves an AWS resource ID to the Terraform address and HCL block that manages it (`locate_block`), by scanning `.tf` files and, where available, `terraform show -json` state. |
| `terraform_editor.py` | Low-level, dependency-free HCL editing: `remove_block`, `set_block_attribute`, `find_references` (so a removed resource's dangling references are caught), `validate_directory` (runs `terraform validate` after an edit before it's proposed as a PR). |

`DateTimeEncoder` in both `ec2_remediator.py` and `resource_remediator.py`
is the JSON encoder used when serializing rollback snapshots (datetimes
aren't JSON-serializable by default).
