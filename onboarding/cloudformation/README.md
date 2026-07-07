# onboarding/cloudformation/

CloudFormation equivalent of [`onboarding/terraform/`](../terraform/), for
accounts onboarded without Terraform.

| File | Purpose |
|---|---|
| `wasteless-onboarding.yaml` | Creates `WastelessReadOnlyRole` (always) and `WastelessRemediationRole` (conditional on `CreateRemediationRole`, default `true`) as a trust relationship to `TrustedPrincipalArn`, optionally gated by `ExternalId`. Outputs `ReadOnlyRoleArn` / `RemediationRoleArn` — paste directly into the wasteless `.env` as `AWS_ROLE_ARN` / `AWS_WRITE_ROLE_ARN`. |

The permission statements are inlined from the same source as the Terraform
module — kept in sync with [`../policies/`](../policies/) by
`tests/unit/test_onboarding_policies.py`. If you edit permissions here,
mirror the change in `../policies/*.json` and the Terraform module, then
re-run that test.

## Deploy

```bash
aws cloudformation deploy \
  --template-file wasteless-onboarding.yaml \
  --stack-name wasteless-onboarding \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      TrustedPrincipalArn=arn:aws:iam::123456789012:user/wasteless \
      ExternalId=a-random-shared-secret

aws cloudformation describe-stacks --stack-name wasteless-onboarding \
  --query 'Stacks[0].Outputs'
```
