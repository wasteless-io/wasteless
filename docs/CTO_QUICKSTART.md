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

**Console path (no tooling):** open
[CloudFormation → Create stack](https://console.aws.amazon.com/cloudformation/home#/stacks/create),
upload the template from [`onboarding/cloudformation/`](../onboarding/cloudformation/),
and create the stack. The **Outputs** tab then shows three values:
`RoleArn`, `WriteRoleArn` (optional), `ExternalId`.

**Terraform path:** apply [`onboarding/terraform/`](../onboarding/terraform/) —
same outputs.

## 3. Start the interface (30 s)

```bash
wasteless
```

Open <http://localhost:8888>. A banner tells you AWS is not connected yet.

## 4. Paste, test, save (1 min)

Follow the banner to <http://localhost:8888/setup>, paste the values from
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
