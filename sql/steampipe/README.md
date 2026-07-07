# sql/steampipe/

Read-only inventory queries run against [Steampipe](https://steampipe.io)'s
AWS plugin tables (not against the wasteless PostgreSQL database). Each file
backs one Steampipe-native detector in `src/detectors/` via
`src/collectors/steampipe.py`'s `run_query_file()`.

Prerequisite: `brew install turbot/tap/steampipe && steampipe plugin install
aws`. Multi-region is configured once in `~/.steampipe/config/aws.spc` — none
of these queries loop over regions manually.

| File | Backs detector | Notes |
|---|---|---|
| `ec2_cpu_daily.sql` | `src/collectors/ec2_metrics_steampipe.py` | Daily CPU utilization per instance over 7 days — a Steampipe alternative to `aws_cloudwatch.py`'s `GetMetricStatistics` calls (network metrics still come from boto3). |
| `ebs_gp2.sql` | `ebs_gp2_migration.py` | Attached gp2 volumes eligible for the gp3 migration (unattached gp2 is excluded — `ebs_orphan.py`, boto3-based, already flags those). |
| `elb_unused.sql` | `elb_unused.py` | Load balancers with no registered targets, or with target groups that exist but are empty. |
| `nat_gateway_unused.sql` | `nat_gateway_unused.py` | NAT gateways in state `available` with zero outbound traffic over 30 days. |
| `vpc_unused.sql` | `vpc_unused.py` | VPCs with zero ENIs — every running resource creates an ENI, so none means nothing runs there. |

Wired into `wasteless.sh collect` (skipped with a warning, not a hard
failure, when the `steampipe` binary isn't installed — see
[src/detectors/README.md](../../src/detectors/README.md)).

## Removed: duplicate EIP/EBS-orphan/snapshot queries

`eip_orphan.sql`, `ebs_orphan.sql`, `snapshot_orphan.sql` and
`ami_backed_snapshots.sql` used to back Steampipe-native duplicates of the
boto3 detectors (`eip_orphan.py`, `ebs_orphan.py`, `snapshot_orphan.py`).
Two implementations of the same detection rule risked silent drift, so the
boto3 versions were kept as canonical and the Steampipe duplicates removed.
If a Steampipe-only need for these resource types comes back, write fresh
queries rather than resurrecting the old ones from git history — pricing
and exclusion logic may have moved on in the boto3 versions since.
