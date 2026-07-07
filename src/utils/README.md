# src/utils/

Maintenance scripts, run manually or from cron — not part of the collect/
detect/remediate pipeline proper.

| File | Purpose |
|---|---|
| `cleanup_orphaned_recommendations.py` | `RecommendationCleaner` — marks a recommendation `obsolete` when its underlying resource no longer exists in AWS (e.g. terminated manually outside wasteless). Supports `--dry-run`. Overlaps in intent with `ui/main.py`'s `sync_aws_job` (every 5 min in the UI process); this is the standalone/cron equivalent, useful when the UI isn't running. |

Wired into cron via `scripts/run_cleanup.sh` — see [scripts/README.md](../../scripts/README.md).
