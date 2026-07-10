# scripts/

One-off data backfills and maintenance scripts.

Scheduling lives in `wasteless.sh` (`wasteless schedule` / `collect`), not
here. A set of cron wrappers (`install_automation.sh`, `run_collector.sh`,
`run_detector.sh`, `run_cleanup.sh`) used to live in this directory; they
were removed because they only ran 2 of the pipeline's 10 steps and drifted
as detectors were added — see [AUTOMATION_GUIDE.md](../docs/AUTOMATION_GUIDE.md).

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
