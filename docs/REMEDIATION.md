# Remediation and controls

This guide describes what happens after Wasteless creates a recommendation.
It is the source of truth for execution modes, policy gates and reversibility.

## Separation between detection and action

In the recommended role-based setup, boto3 collection and detection use
`AWS_ROLE_ARN`, a read-only role limited to Describe, Get and List operations.
Steampipe does not read Wasteless's `.env`; configure its AWS connection to
assume the same read-only role separately.

Real AWS actions use `AWS_WRITE_ROLE_ARN`, a separate optional role. When
`AWS_ROLE_ARN` is configured without a write role, boto3 write requests fail
closed instead of falling back to broader credentials.

Legacy mode remains available when no role ARN is configured. It uses the
default boto3 credential chain for both reads and writes, so its effective
permissions are those of the source identity. See [AWS setup](AWS_SETUP.md)
for the exact policies and migration path.

## Execution modes

Every recommendation type is declared in
`ui/utils/action_registry.py`. Unknown types fall back to `manual`, and a test
fails when a detector introduces an undeclared recommendation type.

| Mode | Recommendation types | What approval does |
|---|---|---|
| **Direct AWS** (`boto3`) | `stop_instance`, `terminate_instance` | Uses the write role to stop or terminate the EC2 instance when dry-run is disabled. |
| **Backend remediator** | `migrate_gp2_to_gp3`, `delete_volume`, `delete_nat_gateway`, `delete_load_balancer` | Re-fetches the live resource, re-validates the waste condition, applies supported policy checks, records pre-action state, then calls AWS when dry-run is disabled. |
| **Manual** | `downsize_instance`, `delete_snapshot`, `release_ip`, `delete_vpc`, `deregister_ami`, `delete_rds_instance`, `downsize_rds_instance`, `delete_rds_snapshot` | Records the decision as a manual task. Wasteless does not call an AWS write API. |

Disabling an automated recommendation type in Settings degrades that type to
manual review instead of silently executing it.

## Optional Terraform routing

When `terraform_pr.enabled` is true, an eligible approval can be routed to a
Terraform pull request before the runtime execution mode is considered. The
route depends on the configured repository, cost threshold, required resource
types and whether Wasteless can map the recommendation to managed Terraform.

An open PR remains pending. A merged PR records the recommendation as approved;
a closed PR records it as rejected. Wasteless does not claim that merging a PR
proves the infrastructure change has already been applied.

## Controls applied by the UI

The recommendation endpoint reads safety settings on the server. Client input
cannot disable dry-run.

1. **Dry-run:** `dry_run: true` is the default. Automated actions simulate and
   remain pending.
2. **Explicit decision:** recommendations must be approved, rejected, dismissed
   or marked as a manual task.
3. **Per-action switch:** a disabled automated action becomes manual.
4. **Grace period:** when dry-run is off, automated approvals wait for the
   configured period and can be cancelled before execution.
5. **Separate write role:** in the recommended role-based setup, automated
   boto3 writes request the remediation role.
6. **Action history:** decisions, scheduled operations, successes, failures and
   cancellations are persisted.

Backend remediators add live-state revalidation, whitelist, confidence,
schedule and global auto-remediation checks. The seven-condition EC2 safeguard
set also includes minimum instance age, minimum idle duration and a per-run
limit. These checks are not presented as a universal set for manual decisions
or every direct execution path.

## Default policy

`install.sh` creates `config/remediation.yaml` from the versioned template.
The Settings page can edit the same policy and export or import it as YAML.

| Setting | Default | Scope |
|---|---:|---|
| `dry_run` | `true` | Prevents automated AWS writes |
| `auto_remediation.enabled` | `false` | Required by backend remediators for real execution |
| `approval.grace_period_days` | `3` | Delay for real automated approvals; `0` means immediate |
| `protection.min_instance_age_days` | `30` | EC2 safeguard |
| `protection.min_idle_days` | `14` | EC2 safeguard |
| `protection.min_confidence_score` | `0.80` | Backend safeguard |
| `protection.max_instances_per_run` | `3` | EC2 blast-radius limit |
| `schedule.enabled` | `false` | Restricts backend remediator execution windows when enabled |
| `terraform_pr.enabled` | `false` | Enables eligible GitOps routing |

Resource IDs and tags can be whitelisted. The default template protects tags
`Environment=Production` and `Critical=true`.

The template also declares `rollback.enabled` and `rollback.retention_days`,
but current remediators do not read those values. They create pre-action state
records unconditionally with a seven-day expiry. Treat both keys as reserved
until execution is wired to them.

## Reversibility

An audit record or state snapshot does not make an AWS operation reversible.

| Action | Execution | Recovery characteristic |
|---|---|---|
| Stop EC2 | Direct AWS | The instance can normally be started again. |
| Terminate EC2 | Direct AWS | Irreversible through Wasteless. |
| Migrate gp2 to gp3 | Backend remediator | Pre-action state is marked rollback-capable; no automatic rollback workflow is currently documented. |
| Delete orphaned EBS volume | Backend remediator | Creates an EBS snapshot first so the volume can be recreated during retention. |
| Delete NAT gateway | Backend remediator | Irreversible; configuration must be recreated manually. |
| Delete load balancer | Backend remediator | Irreversible; configuration must be recreated manually. |
| Manual recommendation | Human-operated | Wasteless records the decision but does not execute or guarantee recovery. |

Always validate real writes in a sandbox account. The
[production validation guide](PRODUCTION_VALIDATION.md) provides a controlled
fixture workflow and mandatory teardown steps.

## Recommendation lifecycle

```text
pending
  ├─ reject ───────────────► rejected
  ├─ dismiss ──────────────► dismissed
  ├─ approve manual ───────► approved_manual ── live sync ──► applied / obsolete
  └─ approve automated
       ├─ dry-run ─────────► pending
       ├─ grace period ────► scheduled ── cancel ────────────► pending
       ├─ success ─────────► approved / applied
       └─ failure ─────────► pending with an action log
```

Exact terminal status depends on the execution path. Live synchronization also
marks recommendations obsolete when the underlying resource disappeared or no
longer matches the recorded state.

## Related documentation

- [AWS setup](AWS_SETUP.md)
- [Automation](AUTOMATION_GUIDE.md)
- [Architecture](ARCHITECTURE.md)
- [Production validation](PRODUCTION_VALIDATION.md)
