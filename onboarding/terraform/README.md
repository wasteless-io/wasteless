# Wasteless onboarding — Terraform module

Creates the IAM roles wasteless assumes in your AWS account:

- `wasteless-readonly` — detection and collection only (Describe/Get/List).
- `wasteless-remediation` (optional) — the write actions wasteless may take
  **after you approve a recommendation** (stop instance, delete orphaned
  volume, …). Skip it with `create_remediation_role = false` for a
  detection-only setup.

The permission policies are read from [`../policies/`](../policies/), the
same JSON files documented action-by-action in
[`docs/AWS_SETUP.md`](../../docs/AWS_SETUP.md).

## Usage

```hcl
module "wasteless_onboarding" {
  source = "github.com/wasteless-io/wasteless//onboarding/terraform"

  # The identity wasteless runs with (IAM user or role ARN)
  trusted_principal_arns = ["arn:aws:iam::123456789012:user/wasteless"]

  # Recommended when the principal lives in another account
  external_id = "a-random-shared-secret"

  # Optional
  # role_name_prefix        = "wasteless"
  # create_remediation_role = true
}

output "wasteless_role_arns" {
  value = {
    AWS_ROLE_ARN       = module.wasteless_onboarding.readonly_role_arn
    AWS_WRITE_ROLE_ARN = module.wasteless_onboarding.remediation_role_arn
  }
}
```

Then paste the two ARNs into the wasteless `.env`:

```bash
AWS_ROLE_ARN=arn:aws:iam::<account>:role/wasteless-readonly
AWS_WRITE_ROLE_ARN=arn:aws:iam::<account>:role/wasteless-remediation
AWS_EXTERNAL_ID=a-random-shared-secret   # only if set above
```

Note: referencing the module via `github.com/...//onboarding/terraform`
works because Terraform clones the whole repository — the `file()` calls
that read `../policies/*.json` resolve inside that clone.
