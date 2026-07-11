# Connect AWS in 10 minutes — the non-technical path

This is the short version of [AWS_SETUP.md](AWS_SETUP.md), written for
someone who runs a company, not a terminal. Five steps, two of which you
can delegate.

**What you are granting:** one read-only IAM role (`wasteless-readonly`,
Describe/Get/List permissions only). WasteLess physically cannot modify,
stop, or delete anything in your account with it. Write access is a
separate optional role you can add later, and every write still requires
an explicit approval in the UI.

---

## 1. Install WasteLess (5 min)

```bash
git clone https://github.com/wasteless-io/wasteless.git && cd wasteless
./install.sh
```

When the installer asks about AWS, pick **option 2 (not yet)** — you will
connect through the web page in step 4, which is easier than the terminal.

## 2. Create the roles in your AWS account (3 min — delegable)

This is the only step that happens inside AWS. If someone on your team
manages your AWS account, forward them this section.

**Console path (no tooling):**

1. Get the template file
   [`wasteless-onboarding.yaml`](../onboarding/cloudformation/wasteless-onboarding.yaml)
   on your machine (it is in the folder you installed WasteLess into, under
   `onboarding/cloudformation/`, or download it from GitHub with the link above).
2. Sign in to the AWS console with an account that can create IAM roles
   (an administrator), then open
   [CloudFormation → Create stack](https://console.aws.amazon.com/cloudformation/home#/stacks/create).
3. Select **Upload a template file**, choose `wasteless-onboarding.yaml`,
   click **Next**.
4. Stack name: `wasteless-onboarding`. The default parameters are right for
   the standard setup (WasteLess analyzing the account it is connected to):
   - `TrustedPrincipalArn` — leave empty.
   - `ExternalId` — leave empty, or type a secret phrase of your choice for
     extra protection; you will paste the **same value** into WasteLess at
     step 4. Recommended if WasteLess runs from a different AWS account.
   - `CreateRemediationRole` — keep `true`. Set `false` for detection-only:
     WasteLess can then never modify anything in the account.

   Click **Next**.
5. Leave the stack options as they are. Tick the checkbox
   **"I acknowledge that AWS CloudFormation might create IAM resources with
   custom names"** when it appears (the template creates two roles named
   `wasteless-readonly` and `wasteless-remediation` — that is all it does),
   then **Next** and **Submit**.
6. Wait about a minute until the stack status shows **CREATE_COMPLETE**
   (refresh if needed), then open the **Outputs** tab and copy:
   - `ReadOnlyRoleArn` → the *read-only role ARN* field in WasteLess;
   - `RemediationRoleArn` → the *remediation role ARN* field (this output
     only exists if you kept `CreateRemediationRole` to `true`).

   If you typed an `ExternalId` at step 4 of the wizard, keep it at hand:
   WasteLess asks for the same value.

**Terraform path:** apply [`onboarding/terraform/`](../onboarding/terraform/) —
the outputs `readonly_role_arn` and `remediation_role_arn` are the same two
ARNs as above.

## 3. Start the interface (30 s)

```bash
wasteless
```

Your browser opens on the setup guide (<http://localhost:8888/setup>)
automatically as long as AWS is not connected yet.

## 4. Paste, test, save (1 min)

On the setup page (also reachable from the banner on any page), paste the values from
step 2, click **Test connection** (you get an immediate ✓ or the exact
error), then **Test & save**. Both configuration files are written for
you; no restart needed.

## 5. Collect (1 min)

```bash
wasteless collect
```

The dashboard fills with your account's waste within a couple of minutes,
and collection re-runs automatically from then on.

---

**Something failed?** The error shown by *Test connection* is the actual
AWS error — most often a typo in the ARN or the stack created in the wrong
account. Details and per-permission explanations: [AWS_SETUP.md](AWS_SETUP.md).
