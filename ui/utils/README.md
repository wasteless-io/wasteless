# ui/utils/

Shared helpers imported by `ui/routes/*.py` and `ui/jobs.py` (see
[CLAUDE.md](../../CLAUDE.md#ui-backend-uimainpy--uiroutes) for how the app
is assembled). Distinct from `src/core/`: this layer is UI-specific
plumbing, not pipeline logic.

| File | Purpose |
|---|---|
| `action_registry.py` | **Single source of truth** for how each `recommendation_type` executes when approved: `'boto3'` (direct EC2 stop/terminate in `routes/recommendations.py`), `'remediator'` (routes through `src/remediators/` — safeguards + rollback + re-verification), or `'manual'` (records the decision, touches nothing). Undeclared types default to `'manual'`; `ui/tests/test_action_registry.py` fails if a detector's recommendation type isn't declared here — adding a detector means consciously picking a mode. |
| `aws_clients.py` | Re-exports `src/core/aws_clients.py`'s client factory for the UI process (root path injected into `sys.path` at runtime — see `remediator.py`). No logic of its own. |
| `aws_sync.py` | `find_vanished_resources()` — existence checks used by the 5-minute `sync_aws_job` (`ui/jobs.py`) to mark recommendations `obsolete` when their resource disappeared from AWS. |
| `remediator.py` | `RemediatorProxy` — bridges the UI process to the backend `src/remediators/` (root venv's package, imported via a `sys.path` hack since UI and backend have separate virtualenvs). `validate_backend_at_startup()` / `check_backend_available()` fail fast if the path is misconfigured; `sanitize_for_json()` handles datetimes/Decimals in API responses. |
| `config_manager.py` | `ConfigManager` — reads/writes `config/remediation.yaml` (the file backing Settings page and `/api/config`, `/api/whitelist`, `/api/policies/*`). `validate_config_value()` / `validate_instance_id()` enforce the same instance-ID format checks the API returns 400 on. `get_notifications()` reads the `notifications` section for `notifications.py`. |
| `notifications.py` | `notify_action_failure()` — emails `notifications.email` when a remediation action fails and `notifications.notify_on_error` is set (SMTP credentials from `.env`, never from the policy YAML). Called from `routes/recommendations.py`'s `/api/actions` and `jobs.py`'s `grace_executor_job` on real (non-dry-run) failures. Silently no-ops if SMTP isn't configured — never blocks or fails the action it's reporting. `notifications.slack_webhook` is accepted by policy import/export but not wired to anything yet (V1 is email-only). |
| `policies.py` | Policy-as-code: `export_policy_yaml()` / `parse_policy_yaml()` / `validate_policy()` back the Settings → "Policy as Code" export/import round-trip. |
| `terraform_pr.py` | `maybe_open_pr()` / `sync_open_prs()` — orchestrates `src/remediators/terraform_pr.py` from the UI's `terraform_pr_sync_job` (every 5 min): opens PRs for routed changes, checks open PRs for merge/close to flip recommendation status. |
| `reports.py` | `resolve_period()` / `report_filename()` — date-range resolution and filename conventions shared by the Reports page and `/api/reports/download`. |
| `log_buffer.py` | `RingBufferHandler` — in-memory ring buffer capturing app logs for the `/logs` debug page and `/api/logs` polling endpoint. Not persisted; resets on restart. |
| `logger.py` | Structured logging setup (`setup_logging`, `get_logger`) plus semantic helpers (`log_user_action`, `log_remediation_action`, `log_security_event`, …) so log lines are consistently shaped across the app. |
