# src/utils/

Maintenance scripts, run manually — not part of the collect/detect/remediate
pipeline proper.

| File | Purpose |
|---|---|
| `cleanup_orphaned_recommendations.py` | `RecommendationCleaner` — marks an EC2 recommendation `obsolete` when its instance no longer exists in AWS (e.g. terminated manually outside wasteless). Supports `--dry-run`. The UI's `sync_aws_job` (every 5 min, all resource types) is the canonical automatic version; this is the standalone equivalent for when the UI isn't running — see [CLEANUP_GUIDE.md](../../docs/CLEANUP_GUIDE.md). |
