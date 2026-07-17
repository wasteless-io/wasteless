# Connect AWS with the guided setup

This is the short version of [AWS_SETUP.md](AWS_SETUP.md), written for
someone who runs a company, not a terminal. Five steps, one of which you
can delegate.

**What you are granting:** one read-only IAM role (`wasteless-readonly`,
Describe/Get/List permissions only). Wasteless physically cannot modify,
stop, or delete anything in your account with it. Write access is a
separate optional role you can add later, and every write still requires
an explicit approval in the UI.

---

## 1. Install Wasteless

```bash
git clone https://github.com/wasteless-io/wasteless.git && cd wasteless
./install.sh
```

When the installer asks about AWS, pick **option 2 (not yet)** — you will
connect through the web page in the next steps, which is easier than the
terminal.

## 2. Open the setup page

```bash
wasteless
```

Your browser opens on the setup guide (<http://localhost:8888/setup>)
automatically as long as AWS is not connected yet.

## 3. Create the roles in your AWS account

This is the only step that happens inside AWS.

On the setup page, click **Create the roles in AWS →**. It opens your AWS
console directly on a pre-filled *Create stack* page. There:

1. Sign in with an account that can create IAM roles (an administrator).
2. Tick the checkbox **"I acknowledge that AWS CloudFormation might create
   IAM resources with custom names"** (the stack creates two roles named
   `wasteless-readonly` and `wasteless-remediation` — that is all it does),
   then click **Create stack**.
3. Wait about a minute until the stack status shows **CREATE_COMPLETE**
   (refresh if needed), then come back to the setup page.

If someone on your team manages your AWS account, use **Copy link** next to
the button and send them the link instead — it carries everything they need.

Two options worth knowing, both on the setup page *before* you open the link:

- **External ID** — type a secret phrase of your choice in the *External ID*
  field for extra protection; the link bakes it into the stack, and Wasteless
  keeps the same value. Recommended if Wasteless runs from a different AWS
  account.
- **Detection-only** — on the AWS review page, set `CreateRemediationRole`
  to `false`: Wasteless can then never modify anything in the account. Clear
  the *Remediation role ARN* field on the setup page afterwards.

<details>
<summary>Manual path (if the pre-filled link is unavailable)</summary>

1. Get the template file
   [`wasteless-onboarding.yaml`](../onboarding/cloudformation/wasteless-onboarding.yaml)
   on your machine (it is in the folder you installed Wasteless into, under
   `onboarding/cloudformation/`, or download it from GitHub with the link above).
2. Sign in to the AWS console with an account that can create IAM roles
   (an administrator), then open
   [CloudFormation → Create stack](https://console.aws.amazon.com/cloudformation/home#/stacks/create).
3. Select **Upload a template file**, choose `wasteless-onboarding.yaml`,
   click **Next**.
4. Stack name: `wasteless-onboarding`. The default parameters are right for
   the standard setup (Wasteless analyzing the account it is connected to):
   - `TrustedPrincipalArn` — leave empty.
   - `ExternalId` — leave empty, or type a secret phrase of your choice; you
     will paste the **same value** into Wasteless at step 4.
   - `CreateRemediationRole` — keep `true`, or `false` for detection-only.

   Click **Next**.
5. Leave the stack options as they are, tick the IAM acknowledgement
   checkbox, then **Next** and **Submit**.
6. Wait for **CREATE_COMPLETE**, then open the **Outputs** tab and copy
   `ReadOnlyRoleArn` (and `RemediationRoleArn` if present) into the matching
   fields of the setup page.

</details>

**Terraform path** *(optional — only if your team already manages its
infrastructure with Terraform; Terraform is never required to install or run
Wasteless)*: apply [`onboarding/terraform/`](../onboarding/terraform/) —
the outputs `readonly_role_arn` and `remediation_role_arn` are the same two
role ARNs.

## 4. Test and save

Back on the setup page: the region and both role ARNs are already filled in
from your account ID and the template's default role names. Click
**Test connection** (you get an immediate ✓ or the exact error), then
**Test & save**. Both configuration files are written for you; no restart
needed.

## 5. Let the first collection run

Nothing to do: a first collection starts automatically the moment you save,
the dashboard fills with your account's waste within a couple of minutes,
and collection re-runs automatically from then on. (`wasteless collect` in
a terminal triggers one manually at any time.)

---

**Something failed?** The error shown by *Test connection* is the actual
AWS error — most often the stack not finished yet, created in another
account, or a remediation role that was skipped (clear that field and test
again). Details and per-permission explanations: [AWS_SETUP.md](AWS_SETUP.md).
