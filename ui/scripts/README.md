# ui/scripts/

Ad-hoc operational/dev scripts for the UI process, not part of the
automated test suite in `ui/tests/`.

| File | Purpose |
|---|---|
| `benchmark.sh` | Measures UI import times and startup performance — run when `uvicorn` start feels slower than expected. |
| `cleanup_performance.sh` | Removes stale `__pycache__` files that can slow startup (especially after switching branches). |
| `integration_runner.py` | Exercises `ui/utils/remediator.py`'s `RemediatorProxy` bridge against the real backend, outside of pytest — useful for manually verifying the cross-venv `sys.path` bridge works after moving the repo or changing venvs. |
| `test_real_execution.py` | Drives a real (non-mocked) remediation execution path — deliberately outside `ui/tests/` since it can perform a real AWS action; run manually and deliberately, not in CI. |
