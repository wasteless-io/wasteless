# src/core/

Shared plumbing imported by collectors, detectors, remediators and the UI.
Nothing here talks to AWS for detection logic itself — that lives in
`src/detectors/`.

| File | Purpose |
|---|---|
| `database.py` | Connection pool (`init_connection_pool`, `get_db_connection`, `get_cursor`), `health_check()`. Everything in `src/` and `ui/` goes through this rather than opening raw psycopg2 connections. |
| `config.py` | `RemediationConfig.from_yaml()` loads `config/remediation.yaml` into typed dataclasses (`AWSConfig`, `DatabaseConfig`, `DetectorConfig`, `TerraformPRConfig`). `get_config()` / `validate_environment()` are the entry points scripts call at startup. |
| `safeguards.py` | `Safeguards` — the 7 sequential checks run before any AWS write action (auto-remediation enabled, not whitelisted, age ≥ 30d, confidence ≥ 0.80, idle ≥ 14d, in schedule window, under the per-run instance cap). Raises `SafeguardException` on the first failed check; see the [Safeguards section of the root README](../../README.md#safeguards). |
| `aws_clients.py` | Central boto3 client factory (`get_client`) with cross-account `AssumeRole` support (`AWS_ROLE_ARN` / `AWS_WRITE_ROLE_ARN` / `AWS_EXTERNAL_ID`). `reset_cache()` is for tests. `ui/utils/aws_clients.py` re-exports this rather than duplicating it. |
| `pricing.py` | `stamp_pricing()` — attaches provenance metadata (unit price, source, as-of date) to a detection so recommendations never show a monthly saving without a traceable price behind it. |
| `finops_invariants/` | Arithmetic/business guard-rails on every number that reaches a human: forecast math, CTO-safe wording (`validate_claim_wording`, `generate_cto_safe_summary`), realized-vs-potential savings, confidence scoring. Package, not a single file — see its own README before adding a new numeric claim anywhere in reports or the UI. |
| `llm.py` | Provider-agnostic AI insights via `litellm` (`generate_insight`, `answer_question`, `enrich_recommendations`). No-ops silently (`is_enabled()` returns `False`) when `WASTELESS_LLM_MODEL` isn't configured — never blocks the pipeline. Usage is metered via `record_usage()` → `llm_usage` table. |
| `snapshots.py` | `snapshot_active_waste()` — writes one row per resource type per day into `waste_snapshots`, the source for the dashboard's waste trend and waste-by-resource charts (see `ui/main.py:fetch_waste_by_resource`). |

`config.py`'s `RemediationConfig` and `finops_invariants/`'s validators are
the two modules most likely to need a look whenever behavior around money or
safety changes.
