# scripts/

Operational scripts: cron wrappers around the `src/` pipeline and one-off
data backfills.

## Cron wrappers (installed by `install_automation.sh`)

| File | Purpose |
|---|---|
| `install_automation.sh` | Installs all cron jobs below in one step — the "full automation" setup. |
| `run_collector.sh` | Runs `src/collectors/aws_cloudwatch.py`, logs output. Cron example: daily at 2 AM. |
| `run_detector.sh` | Runs `src/detectors/ec2_idle.py`, logs output. |
| `run_cleanup.sh` | Runs `src/utils/cleanup_orphaned_recommendations.py` (marks recommendations `obsolete` when the AWS resource is gone). |

All three check that `.env` exists (the Python entrypoints load it
themselves via `load_dotenv` — the shell never `source`s it), `cd` to the
project root, and append to their own log file — check those logs first
when a cron run is suspected to have failed silently.

## One-off / maintenance

| File | Purpose |
|---|---|
| `backfill_waste_snapshots.py` | One-shot backfill of `waste_snapshots` from resource creation dates, for dashboards on a database that predates that table. Run once after deploying `sql/migrations/waste_snapshots.sql` on existing data. |
| `store_aws_real_monthly_cost.py` | Pulls AWS Cost Explorer billing data (multi-account via Organizations, falls back to current account) into `cloud_costs_raw` — the "real spend" counterpart to the detectors' estimated waste. |
| `github_users.sh` | Manages GitHub repo collaborators via `gh` CLI (requires `gh auth login`). Unrelated to the AWS pipeline — a repo-admin convenience script. |

Scripts here must go through the product flow (detect → recommend →
safeguards → act). A standalone multi-cloud cleanup CLI (`finops.sh`) used
to live here; it was removed because it deleted resources directly,
bypassing every safeguard the product exists to provide, and was tied to
infrastructure unrelated to this repo.
