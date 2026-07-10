# src/

Backend pipeline: collect AWS data, detect waste, remediate, verify savings.
Runs in its own virtualenv (root `venv/`), separate from `ui/`. See the
[root README](../README.md) for the full data flow diagram and
[docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md) for the exact commands.

```
collectors/   →  write to ec2_metrics / cloud_costs_raw
detectors/    →  read metrics, write waste_detected + recommendations
remediators/  →  execute approved recommendations against AWS (guarded)
trackers/     →  verify savings actually happened (Cost Explorer)
reports/      →  assemble Markdown/AI reports from the database
core/         →  shared plumbing: db, config, safeguards, pricing, llm
utils/        →  one-off maintenance scripts
```

- `aws_collector.py` — Cost Explorer collector, writes daily per-service costs
  to `cloud_costs_raw` (multi-account via AWS Organizations, falls back to the
  current account).
- `constants.py` — shared numeric thresholds (CPU %, idle days, pricing
  fallbacks) imported across collectors/detectors so they don't drift.

Every detector is a standalone script (`python3 src/detectors/<name>.py`) —
there is no central registry or scheduler in `src/`; `wasteless.sh collect`
(repo root) and `ui/main.py`'s APScheduler jobs are what actually invoke
these on a schedule. See each subfolder's README for what is and isn't wired
in.
