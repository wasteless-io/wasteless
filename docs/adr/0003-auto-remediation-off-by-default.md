# ADR-0003 — Auto-remediation off by default; dry-run first, 7 safeguards

Status: accepted

## Context

wasteless can stop, terminate, and delete real AWS resources. A wrong action is
expensive and hard to undo. The tool is also open-source and self-hosted, so it
runs in accounts we don't control and can't monitor.

## Decision

- **Auto-remediation is disabled by default** (`auto_remediation.enabled: false`).
  Every action defaults to **dry-run**.
- Before any AWS write, `src/core/safeguards.py` runs **7 sequential checks**:
  (1) auto-remediation enabled, (2) instance not whitelisted, (3) age ≥ 30 days,
  (4) confidence ≥ 0.80, (5) idle ≥ 14 days, (6) within the allowed schedule,
  (7) instances stopped this run < max limit. Any failure aborts and is logged.
- Every write captures a `rollback_snapshots` row so the action can be reversed.

## Consequences

- The core value (acting on AWS) ships **off**, so it must be deliberately
  validated on a sandbox before anyone trusts it — see
  [PRODUCTION_VALIDATION.md](../PRODUCTION_VALIDATION.md).
- Safeguard thresholds live in `config/remediation.yaml`; the defaults are
  conservative on purpose. Relaxing them for a fast validation is a documented,
  reversible exception, not a new default.
- `safeguards.py` is one of the few files under a blocking mypy gate (ADR-0005).
