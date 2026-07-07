# tests/integration/

Tests that need real infrastructure — a live PostgreSQL and, for one file, a
real AWS account. Not mocked, not run by default in environments without
that infrastructure provisioned.

| File | Requires | Covers |
|---|---|---|
| `test_ui_coherence.py` | Real Postgres (`wasteless-postgres` container) | Cross-page KPI consistency — the same number (e.g. total detected waste) must match wherever it's shown across Home/Dashboard/Recommendations. Locks in the Home/Dashboard equality fix (see `dcc1d60` in git log). |
| `test_llm_report_generation.py` | LLM provider configured (`WASTELESS_LLM_MODEL`) | Golden-snapshot tests for the AI narrative layer of the audit report — compares generated output against `tests/snapshots/golden_aws_audit_report.md`. |
| `test_real_aws_account_audit.py` | Real AWS credentials, read-only | Audit layer 5: runs the read-only audit against an actual AWS account rather than fixtures, to catch anything the mocked unit tests can't (real API shapes, real pagination, real throttling). |

Run explicitly (not part of a quick `pytest tests/unit/`):
```bash
source venv/bin/activate
pytest tests/integration/
```
