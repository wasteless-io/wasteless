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

## Hosted copy (quick-create link)

The `/setup` page of the UI offers a one-click **CloudFormation quick-create
link**. The console requires `templateURL` to be an S3 HTTPS URL, so a public
copy of this template is hosted at:

```
https://wasteless-io-onboarding.s3.eu-west-1.amazonaws.com/latest/wasteless-onboarding.yaml
```

Maintainers publish it with
[`scripts/publish_onboarding_template.sh`](../../scripts/publish_onboarding_template.sh)
(creates the bucket on first run — policy-based public `GetObject` only —
then uploads `latest/` and, with a version argument, `v<X.Y.Z>/`). Re-run it
after every change to the template, ideally as part of the release process.

Forks and private mirrors can point the UI elsewhere with the
`WASTELESS_ONBOARDING_TEMPLATE_URL` environment variable (in `ui/.env`).
