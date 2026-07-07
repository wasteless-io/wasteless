# tests/snapshots/

Golden-file outputs compared byte-for-byte (or close to it) against what the
code generates today.

| File | Compared against |
|---|---|
| `golden_aws_audit_report.md` | `src/reports/audit_report.py`'s `generate_audit_report()`, fed with `tests/fixtures/golden_aws_audit_dataset.json`, exercised by `tests/integration/test_llm_report_generation.py`. |

A diff here means one of two things: a real regression in report assembly,
or a deliberate change to the report format/wording — in the latter case,
regenerate and commit the new snapshot in the same PR as the code change so
the diff is reviewable, rather than editing it separately.
