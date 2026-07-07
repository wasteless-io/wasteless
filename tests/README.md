# tests/

Backend test suite (root `venv/`), run with `pytest` from the repo root. UI
tests are separate — see [`ui/tests/README.md`](../ui/tests/README.md).

```
unit/          →  fast, no external dependencies (mocked AWS/DB)
integration/   →  hit a real Postgres and/or a real AWS account
fixtures/      →  static JSON datasets shared across unit tests
snapshots/     →  golden-file outputs compared byte-for-byte
test_end_to_end.py  →  full pipeline smoke test (collect → detect → remediate → verify)
```

| Path | Purpose |
|---|---|
| `test_end_to_end.py` | Exercises the complete flow described in its own docstring: CloudWatch collection → idle detection → recommendation generation → remediation execution → savings verification. The closest thing to a manual QA pass, automated. |
| [`unit/`](unit/) | Bulk of the suite — see its own README for the file-by-file map. |
| [`integration/`](integration/) | Tests that need real infrastructure (a live Postgres, or a real AWS account) — not run by default CI unless those are provisioned. |
| [`fixtures/`](fixtures/) | Shared static datasets (golden AWS audit dataset, valid/invalid FinOps datasets, pricing edge cases, dangerous-recommendation examples) consumed by both `unit/` and `integration/`. |
| [`snapshots/`](snapshots/) | Golden Markdown output compared against what `src/reports/audit_report.py` generates today — a diff means either a regression or an intentional report-format change (update the snapshot deliberately, don't silence the test). |

Run:
```bash
source venv/bin/activate
pytest                              # everything
pytest tests/unit/test_safeguards.py
pytest -k test_confidence
```
