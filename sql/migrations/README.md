# sql/migrations/

Schema changes applied after `init.sql` + `ec2_metrics.sql`, one file per
change, run in filename order by `install.sh` (section "5/7"). Each file is
idempotent (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`) so re-running the
whole set on an already-migrated database is safe.

| File | What it adds |
|---|---|
| `remediation_tables.sql` | Core auto-remediation tables: `waste_detected`, `recommendations`, `actions_log`, `rollback_snapshots`. |
| `add_missing_indexes.sql` | Performance indexes added after profiling slow queries. |
| `ai_insight_column.sql` | `recommendations.ai_insight` — LLM explanation text, filled only when `WASTELESS_LLM_MODEL` is set (`src/core/llm.py`). |
| `llm_usage.sql` | `llm_usage` — one row per LLM call (insights, narratives), cost computed by litellm in USD, displayed in EUR via a fixed `USD_TO_EUR` rate. |
| `grace_period.sql` | Adds the `scheduled` status + `execute_after`: an approved recommendation can wait out a cancellable grace period before execution. |
| `cloud_costs_unique.sql` | Uniqueness constraint on `cloud_costs_raw` so the two writers (`src/aws_collector.py` per-service rows, `scripts/store_aws_real_monthly_cost.py` billing totals) can't double-count the same day/service. |
| `daily_briefings.sql` | `daily_briefings` — one row per day, caches the AI-generated CTO status message so `src/reports/daily_briefing.py` calls the LLM at most once/day. |
| `terraform_pr.sql` | Adds the `pr_open` recommendation status for the Terraform GitOps remediation flow (`src/remediators/terraform_pr.py`). |
| `waste_snapshots.sql` | `waste_snapshots` — daily photograph of active waste per resource type, feeding the dashboard's trend chart (distinct from `waste_detected.updated_at`, which moves on every re-scan). |
| `gp2_waste_is_delta.sql` | Data-fix migration: corrects historical gp2 detections that stored the volume's full monthly cost instead of just the gp2→gp3 price delta as `monthly_waste_eur`. |
| `active_waste_view.sql` | `active_waste` view — detected waste whose resource still exists and hasn't been remediated (excludes `obsolete`/resolved recommendations). This is the source of truth for "how much waste is there right now" across the UI. |

When adding a new migration: pick the next chronological filename, keep the
`IF NOT EXISTS` idempotency guard, and add a one-line comment header
explaining *why* (not just what) — this is what future contributors read
instead of asking you.
