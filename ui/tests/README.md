# ui/tests/

UI test suite, run with `cd ui && python run_tests.py` (own virtualenv,
separate from the backend's `tests/`). Mostly regression tests pinned to a
specific bug that was found and fixed — the docstring usually names the
behavior being locked in, which is often more informative than the file
name alone.

| File | Covers |
|---|---|
| `test_action_registry.py` | `ui/utils/action_registry.py` — every recommendation type has a consciously declared execution mode. |
| `test_action_status_guards.py` | Regression: `POST /api/actions` reject/dismiss can't overwrite a recommendation that's already in a resolved status. |
| `test_grace_executor.py` | `_grace_execution_status()` in `main.py` — the decision helper for executing a recommendation once its grace period elapses. |
| `test_grace_period.py` | The approval grace period end-to-end — `ConfigManager` accessors + scheduling behavior. |
| `test_sync_ec2_states.py` | `_sync_ec2_instance_states()` in `main.py` — reconciling recommendation status with live EC2 state (integration-style, hits real logic paths). |
| `test_aws_sync.py` | `ui/utils/aws_sync.py` — existence checks behind the 5-min sync job. |
| `test_terraform_pr.py` | `ui/utils/terraform_pr.py` — approval routing and PR lifecycle sync. |
| `test_cloud_resources_pagination.py` | Regression: `GET /cloud-resources` pagination (boto3 calls paginate correctly and totals reflect the full result set, not just the displayed page). |
| `test_page_truncation_totals.py` | Regression: `/history` and the Scheduled/PR-open sections don't silently truncate totals when a list is capped for display. |
| `test_config_manager.py` | `ui/utils/config_manager.py` — reading/writing `config/remediation.yaml`. |
| `test_policies.py` | `ui/utils/policies.py` — policy-as-code export/import round-trip. |
| `test_remediator.py` | `ui/utils/remediator.py` — the `RemediatorProxy` bridge to the backend. |
| `test_reports.py` | `ui/utils/reports.py` — date-range resolution and report filenames. |
| `test_briefing.py` | The daily CTO briefing (`src/reports/daily_briefing.py`) as surfaced through the UI. |
| `test_log_buffer.py` | `ui/utils/log_buffer.py` — in-memory ring buffer capture. |
| `test_logger.py` | `ui/utils/logger.py` — structured logging helpers. |
| `test_integration.py` | Broader integration tests exercising multiple UI routes/utils together. |
