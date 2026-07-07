# tests/integration/

Tests that need real infrastructure — a live PostgreSQL and, for two files,
real AWS credentials / a real LLM provider.

| File | Requires | Covers | Runs in CI? |
|---|---|---|---|
| `test_ui_coherence.py` | Real Postgres | Cross-page KPI consistency — the same number (e.g. total detected waste) must match wherever it's shown across Home/Dashboard/Recommendations. Locks in the Home/Dashboard equality fix (see `dcc1d60` in git log). | Yes |
| `test_db_backup_restore.py` | Real Postgres + `pg_dump`/`psql` (docker exec locally, host binaries in CI) | Reproduces `docs/DEPLOYMENT.md`'s backup/restore procedure end to end against a throwaway database, created and dropped inside the same Postgres instance — proves the dump is actually restorable with matching row counts, not just that the commands exit 0. Never touches the real `wasteless` database. | Yes |
| `test_llm_report_generation.py` | LLM provider configured (`WASTELESS_LLM_MODEL`) | Golden-snapshot tests for the AI narrative layer of the audit report — compares generated output against `tests/snapshots/golden_aws_audit_report.md`. | No — skips (no LLM key in CI by design) |
| `test_real_aws_account_audit.py` | Real AWS credentials, read-only | Audit layer 5: runs the read-only audit against an actual AWS account rather than fixtures, to catch anything the mocked unit tests can't (real API shapes, real pagination, real throttling). | No — skips (no AWS credentials in CI by design; giving CI real cloud creds is a deliberate decision, not a default) |

`.github/workflows/tests.yml`'s `backend` job runs a `postgres:16-alpine`
service and applies the full schema (`sql/init.sql` + `sql/ec2_metrics.sql`
+ `sql/migrations/*.sql`) before `pytest -q`, so the two Postgres-only
tests above run for real on every push/PR — "CI green" is no longer just
the unit tests. The client tools installed in CI (`postgresql-client-16`)
are pinned to match the service's major version: a newer `pg_dump` can
emit settings (e.g. `transaction_timeout`) an older server rejects on
restore, which is exactly what `test_db_backup_restore.py`'s TCP fallback
path caught locally during development (Homebrew's `psql`/`pg_dump` 17
against the project's Postgres 16 container).

Run explicitly (not part of a quick `pytest tests/unit/`):
```bash
source venv/bin/activate
pytest tests/integration/
```
