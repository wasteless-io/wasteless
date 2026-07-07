# terraform/

Two independent things live in this folder — don't confuse them:

| Path | Purpose |
|---|---|
| `main.tf` (this folder) | A single ad-hoc `t2.micro` EC2 instance (`wasteless-test-instance`), used for quick manual checks against a real running instance. Not part of the detector validation suite. |
| [`test-fixtures/`](test-fixtures/) | The real, tagged, billed fixtures that validate each detector one-to-one (Elastic IP, NAT gateway, gp2 volume, unused ALB, orphaned volume, empty VPC). Has its own README with the full fixture table and the mandatory `terraform destroy` reminder. |

## Usage (this folder)

```bash
cd terraform
terraform init
terraform apply     # ~free-tier t2.micro, remember to destroy
terraform destroy
```

`terraform.tfstate` / `.tfstate.backup` are committed here (test/throwaway
infra, not the onboarding roles) — treat state conflicts carefully if two
people run `apply` against this folder at the same time.

For the IAM roles wasteless assumes in a target account, see
[`onboarding/terraform/`](../onboarding/terraform/) instead — a different
module entirely.
