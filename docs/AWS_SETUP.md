# AWS Setup Guide for Wasteless

> **Connect your AWS account to wasteless — without fear.**

Version: 2.0
Last Updated: July 2026
Estimated Time: 10-15 minutes

---

## 🔐 How wasteless accesses AWS

The recommended Wasteless setup uses **two separate IAM roles** in your
account, assumed via `sts:AssumeRole`:

```
                       sts:AssumeRole
wasteless ──────────────────────────────────────► your AWS account
   │
   ├── AWS_ROLE_ARN        → wasteless-readonly     (recommended)
   │                          Describe/Get/List only.
   │                          Collection + detection. Cannot modify anything.
   │
   └── AWS_WRITE_ROLE_ARN  → wasteless-remediation  (optional)
                              Assumed ONLY for a remediation action
                              you approved in the UI.
```

Key guarantees:

- **Read-only in the recommended boto3 path.** When `AWS_ROLE_ARN` is set,
  boto3 detection and collection assume `wasteless-readonly`. Configure
  Steampipe separately to assume the same role. Without a remediation role,
  this role-based setup cannot perform boto3 writes.
- **Write is opt-in and fail-closed in role-based mode.** If `AWS_ROLE_ARN` is set but
  `AWS_WRITE_ROLE_ARN` is not, any write action raises a configuration
  error — wasteless never falls back to broader credentials for a write.
- **Controls on top.** Even with the write role, dry-run and automatic
  remediation are disabled for real writes by default. Exact controls depend
  on whether an action is direct, handled by a backend remediator, routed to
  Terraform, or manual. See [Remediation and controls](REMEDIATION.md).
- **Short-lived assumed sessions.** Credentials returned by STS are temporary
  and refreshed automatically. The source identity may still use long-lived
  keys; prefer an instance profile or another short-lived source.
- **Auditable role-based calls.** boto3 calls appear in CloudTrail under
  `assumed-role/wasteless-readonly/...` or
  `assumed-role/wasteless-remediation/...`. Steampipe follows the identity in
  its own connection configuration.

The exact permissions are versioned in
[`onboarding/policies/`](../onboarding/policies/) and explained
action-by-action [below](#-permissions-reference).

---

## 🚀 Quick onboarding

Pick one of the three options. Each creates the two roles and outputs the
ARNs to paste into your `.env`.

### Option A — CloudFormation (1 click)

1. Open the CloudFormation console in the target account and create a
   stack from
   [`onboarding/cloudformation/wasteless-onboarding.yaml`](../onboarding/cloudformation/wasteless-onboarding.yaml),
   or use the quick-create URL pattern:

   ```
   https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=<raw-template-url>&stackName=wasteless-onboarding
   ```

2. Parameters:
   - `TrustedPrincipalArn` — the identity wasteless runs with
     (e.g. `arn:aws:iam::123456789012:user/wasteless`). Leave empty to
     trust your own account root (single-account, self-hosted).
   - `ExternalId` — optional shared secret, recommended for
     cross-account trust ([why?](#-what-is-externalid)).
   - `CreateRemediationRole` — set to `false` for detection-only.

3. Copy the stack **Outputs** (`ReadOnlyRoleArn`, `RemediationRoleArn`)
   into `.env` (see [Configure wasteless](#%EF%B8%8F-configure-wasteless)).

CLI equivalent:

```bash
aws cloudformation deploy \
  --template-file onboarding/cloudformation/wasteless-onboarding.yaml \
  --stack-name wasteless-onboarding \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides TrustedPrincipalArn=arn:aws:iam::123456789012:user/wasteless
aws cloudformation describe-stacks --stack-name wasteless-onboarding \
  --query 'Stacks[0].Outputs'
```

### Option B — Terraform module

```hcl
module "wasteless_onboarding" {
  source                 = "github.com/wasteless-io/wasteless//onboarding/terraform"
  trusted_principal_arns = ["arn:aws:iam::123456789012:user/wasteless"]
  external_id            = "a-random-shared-secret"  # optional
}
```

See [`onboarding/terraform/README.md`](../onboarding/terraform/README.md).

### Option C — Manual (aws cli)

```bash
# Trust policy: who may assume the roles
cat > /tmp/trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::123456789012:user/wasteless"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role --role-name wasteless-readonly \
  --assume-role-policy-document file:///tmp/trust.json --max-session-duration 3600
aws iam put-role-policy --role-name wasteless-readonly \
  --policy-name wasteless-readonly \
  --policy-document file://onboarding/policies/readonly.json

# Optional write role (also gets the read policy: remediators describe before acting)
aws iam create-role --role-name wasteless-remediation \
  --assume-role-policy-document file:///tmp/trust.json --max-session-duration 3600
aws iam put-role-policy --role-name wasteless-remediation \
  --policy-name wasteless-readonly \
  --policy-document file://onboarding/policies/readonly.json
aws iam put-role-policy --role-name wasteless-remediation \
  --policy-name wasteless-remediation \
  --policy-document file://onboarding/policies/remediation.json
```

---

## ⚙️ Configure wasteless

### 1. `.env` (root and `ui/.env`)

```bash
AWS_REGION=eu-west-1
AWS_ACCOUNT_ID=123456789012

AWS_ROLE_ARN=arn:aws:iam::123456789012:role/wasteless-readonly
AWS_WRITE_ROLE_ARN=arn:aws:iam::123456789012:role/wasteless-remediation
# Only if the roles were created with an ExternalId:
# AWS_EXTERNAL_ID=a-random-shared-secret
```

The identity that *assumes* the roles (the "source credentials") comes
from the default AWS credential chain: `~/.aws/credentials`, environment
variables, or an instance profile. It must be allowed to call
`sts:AssumeRole` on the target roles. In cross-account setups this requires an
identity policy on the source side in addition to the target role's trust
policy.

### 2. Steampipe (`~/.steampipe/config/aws.spc`) — required in tandem

Several detectors collect through [Steampipe](https://steampipe.io),
which does **not** read wasteless's `.env`. If you skip this step,
Steampipe-based detectors silently use whatever (possibly broader)
credentials are configured locally. Mirror the role there:

```hcl
connection "aws" {
  plugin      = "aws"
  role_arn    = "arn:aws:iam::123456789012:role/wasteless-readonly"
  external_id = "a-random-shared-secret"   # only if set
  regions     = ["eu-west-1"]
}
```

### 3. Verify

```bash
source venv/bin/activate
python3 -c "from src.core.aws_clients import get_client; \
print(get_client('sts').get_caller_identity()['Arn'])"
# → arn:aws:sts::123456789012:assumed-role/wasteless-readonly/wasteless
```

---

## 📜 Permissions reference

Every action below is called by actual wasteless code — nothing more is
requested. Source of truth:
[`onboarding/policies/readonly.json`](../onboarding/policies/readonly.json)
and [`remediation.json`](../onboarding/policies/remediation.json).

### Read-only role (`wasteless-readonly`)

| Action | Used by | Why |
|---|---|---|
| `ce:GetCostAndUsage` | cost collector, savings tracker | Retrieve billed costs and verify realized savings |
| `cloudtrail:LookupEvents` | resource history (Recommendations page) | Read-only: who created/touched a resource, last 90 days — context before approving. Stacks created before this permission existed still work; the history panel just explains how to enable it |
| `cloudwatch:GetMetricStatistics` | CloudWatch collector | CPU/network metrics to detect idle instances |
| `ec2:DescribeInstances` | idle/stopped detectors, UI sync | List instances and their state |
| `ec2:DescribeVolumes` | EBS orphan detector | Find unattached volumes |
| `ec2:DescribeAddresses` | EIP orphan detector | Find unassociated Elastic IPs |
| `ec2:DescribeSnapshots` | snapshot orphan detector | Find old snapshots |
| `ec2:DescribeImages` | snapshot orphan detector | Exclude snapshots backing an AMI |
| `ec2:DescribeNatGateways` | NAT gateway detector, UI sync | Find unused NAT gateways |
| `ec2:DescribeVpcs` | Cloud Resources page | Inventory display |
| `elasticloadbalancing:Describe*` (3 actions) | ELB detector | Find load balancers with no healthy targets |
| `s3:ListAllMyBuckets`, `s3:GetBucketLocation` | Cloud Resources page | Inventory display |

Notes:
- `sts:GetCallerIdentity` (startup probe) requires **no IAM permission**.
- `organizations:ListAccounts` is deliberately **not** included: only the
  optional `scripts/store_aws_real_monthly_cost.py` multi-account script
  uses it, and it must run with management-account credentials.

### Remediation role (`wasteless-remediation`)

Only ever assumed for an action **you approved** (or explicitly enabled
in auto-remediation).

| Action | Remediation | Why |
|---|---|---|
| `ec2:StopInstances` | idle instance | The core "stop wasting" action |
| `ec2:StartInstances` | rollback | Undo a stop if you change your mind |
| `ec2:TerminateInstances` | stopped instance | Only for explicitly approved terminations |
| `ec2:ModifyVolume` | gp2 → gp3 migration | Cheaper volume type, no downtime |
| `ec2:CreateSnapshot` + scoped `ec2:CreateTags` | before volume deletion | Rollback snapshot, tagged for traceability |
| `ec2:DeleteVolume` | orphaned volume | After the rollback snapshot succeeds |
| `ec2:DeleteNatGateway` | unused NAT gateway | ~32 €/month each |
| `ec2:ReleaseAddress` | orphaned Elastic IP | Unassociated EIPs are billed |
| `elasticloadbalancing:DeleteLoadBalancer` | unused ELB | No healthy targets |

**Why `Resource: "*"`?** Wasteless can't know in advance which instance
will turn out to be idle. Scoping happens at a different layer: the
whitelist (tags/IDs) in `config/remediation.yaml`, the 7 safeguard
checks, and your explicit approval. If you want IAM-level scoping too,
add a `Condition` on tags to the role policy — wasteless will simply
receive an `AccessDenied` for excluded resources and report it.

---

## 🔑 What is ExternalId?

A shared secret the caller must present in `sts:AssumeRole`, preventing
the [confused deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html).

- **Self-hosted, same account**: optional.
- **Wasteless runs in another account** (ops account, SaaS): recommended.
  Generate any random string, set it in the onboarding template/module
  *and* in `AWS_EXTERNAL_ID`.

---

## 🧓 Legacy mode: IAM user with static keys (deprecated)

If `AWS_ROLE_ARN` is not set, wasteless uses the default boto3 credential
chain as before — an IAM user with `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
in `.env` keeps working. This mode is deprecated because long-lived keys
with broad scope are precisely what makes connecting a cost tool scary.
Migrate by deploying the onboarding stack and setting the two role ARNs. The
source identity should then retain only the permissions required to call
`sts:AssumeRole` on those roles.

---

## ❓ FAQ

**Does the Cost Explorer API cost money?**
Cost Explorer API requests can be billed. Wasteless caches daily data to avoid
repeating a paid request unnecessarily; consult current AWS pricing for the
account and region.

**Why does the savings tracker talk to us-east-1?**
Cost Explorer is only served from `us-east-1`, regardless of your
region. This is an AWS constraint, not a data-location choice.

**Can wasteless delete something I didn't approve?**
The UI only routes write-capable recommendations after approval. Dry-run is
enabled by default, automated actions can use a cancellable grace period, and
in the recommended role-based setup a missing `wasteless-remediation` role
causes boto3 writes to fail closed. Legacy credentials inherit their existing
permissions. Review the exact mode and controls for each action in
[REMEDIATION.md](REMEDIATION.md).

**How do I revoke wasteless's access instantly?**
Delete the CloudFormation stack (or `terraform destroy` the module).
Both roles disappear and role-based boto3 calls fail. Revoke any separate
Steampipe or legacy source credentials independently.
`./uninstall.sh` also offers this cleanup as its last step, using your
own AWS credentials (the wasteless roles cannot delete themselves).

---

## ✅ Setup checklist

- [ ] Onboarding stack/module deployed in the target account
- [ ] `AWS_ROLE_ARN` (and optionally `AWS_WRITE_ROLE_ARN`) in `.env`
- [ ] `AWS_EXTERNAL_ID` set if the roles require it
- [ ] `~/.steampipe/config/aws.spc` mirrors the read-only role
- [ ] `get_caller_identity` shows `assumed-role/wasteless-readonly/...`
- [ ] Cost Explorer enabled in the account (first activation takes ~24h)

## 📚 Additional resources

- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Cross-account access with roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_aws-accounts.html)
- [Architecture](ARCHITECTURE.md) · [Remediation](REMEDIATION.md) · [README](../README.md) · [Development](DEVELOPMENT.md)
