# src/trackers/

Closes the loop on remediation: verifies that an approved action actually
produced the savings a recommendation claimed.

| File | Purpose |
|---|---|
| `savings_tracker.py` | `SavingsTracker` — compares AWS Cost Explorer spend before/after a remediation against the recommendation's estimated saving, writes the confirmed delta to `savings_realized`. Run standalone (`python3 src/trackers/savings_tracker.py`) or scheduled alongside the other pipeline steps. |

This is what backs the "Savings Tracking" feature in the root
[README](../../README.md) and the realized-vs-potential distinction enforced
by `src/core/finops_invariants.py`.
