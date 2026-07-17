# src/detectors/

Each detector is a standalone script: query AWS/DB, score confidence,
`INSERT` into `waste_detected`, then insert linked rows into
`recommendations`. Two collection styles coexist — see which pattern to
follow before adding a new one.

## boto3-based, wired into `wasteless.sh collect`

| File | Detects |
|---|---|
| `ec2_idle.py` | `EC2IdleDetector` — running instances with avg CPU < threshold over the observation window (`validate_cpu_threshold`, `validate_days` guard the inputs). |
| `ec2_stopped.py` | `EC2StoppedDetector` — instances stopped for N+ days (still billed for attached EBS). |
| `ebs_orphan.py` | `EBSOrphanDetector` — unattached EBS volumes. **Canonical** implementation (the Steampipe-based `ebs_orphan_steampipe.py` variant was removed — two implementations of the same rule were a maintenance risk). |
| `eip_orphan.py` | `EIPOrphanDetector` — unassociated Elastic IPs. **Canonical** (same reasoning; `eip_orphan_steampipe.py` removed). |
| `snapshot_orphan.py` | `SnapshotOrphanDetector` — EBS snapshots older than the retention threshold. **Canonical** (`snapshot_orphan_steampipe.py` removed). |

## Steampipe-based, wired into `wasteless.sh collect`

Require the `steampipe` CLI (see `src/collectors/README.md`). No boto3
equivalent exists for these — Steampipe is the only implementation, not a
duplicate:

| File | Detects |
|---|---|
| `elb_unused.py` | `ELBUnusedDetector` — load balancers with zero registered targets. |
| `nat_gateway_unused.py` | `NATGatewayUnusedDetector` — NAT gateways with no outbound traffic in 30 days. |
| `vpc_unused.py` | `VPCUnusedDetector` — VPCs with zero ENIs. |
| `ebs_gp2_migration.py` | `EBSGp2MigrationDetector` — attached gp2 volumes that should migrate to gp3. |
| `ami_orphan.py` | `AMIOrphanDetector` — private AMIs no longer referenced by an instance or launch template. |
| `rds_stopped.py` | `RDSStoppedDetector` — stopped RDS instances that still retain billable storage. |
| `rds_idle.py` | `RDSIdleDetector` — running RDS instances with no observed connections during the analysis window. |
| `rds_snapshot_orphan.py` | `RDSSnapshotOrphanDetector` — old manual RDS snapshots. |

Since these steps require `steampipe`, `wasteless.sh collect` runs them only
if the binary is on `PATH` — see `src/collectors/README.md` for the
prerequisite and how a missing binary is handled (skipped with a warning,
not a hard failure of the whole collect run).

## Base class

`steampipe_base.py` — `SteampipeWasteDetector`: subclass it and implement
only `map_rows()` to turn a `sql/steampipe/<name>.sql` result set into
`waste_detected` rows. This is the pattern for any new Steampipe detector
(see `vpc_unused.py` for the minimal example) — write the SQL, subclass,
declare the `recommendation_type` in `ui/utils/action_registry.py`, wire it
into `wasteless.sh collect`.

## On duplication

Before adding a Steampipe-based detector for a resource type that already
has a boto3 detector (or vice versa), don't — pick one. Two implementations
of the same waste-detection rule drift silently over time (thresholds,
exclusions, edge cases fixed in one and not the other) with no test to
catch the divergence. `ebs_orphan`, `eip_orphan` and `snapshot_orphan` used
to have both; only the boto3 version remains.
