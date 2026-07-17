# src/trackers/

Verifies Cost Explorer savings for eligible successful EC2 stop actions after
at least seven days of post-action data.

| File | Purpose |
|---|---|
| `savings_tracker.py` | `SavingsTracker` — compares AWS Cost Explorer spend before and after an eligible EC2 stop against the recommendation's estimate and writes the confirmed delta to `savings_realized`. Run it with `./venv/bin/python src/trackers/savings_tracker.py`. It is not currently part of `wasteless collect` or the default scheduler. |

This is what backs the "Savings Tracking" feature in the root
[README](../../README.md) and the realized-vs-potential distinction enforced
by `src/core/finops_invariants.py`.
