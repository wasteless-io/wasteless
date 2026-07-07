# ui/routes/

One FastAPI `APIRouter` per page/domain, included into the app by
`ui/main.py`. Extracted from what used to be a single 2223-line `main.py` —
see [CLAUDE.md](../../CLAUDE.md#ui-backend-uimainpy--uiroutes) for the
rationale and the shared-state modules (`ui/state.py`, `ui/jobs.py`,
`ui/schemas.py`) these all depend on.

| Module | Routes | Notes |
|---|---|---|
| `home.py` | `GET /`, `GET /landing` | Home overview KPIs + public landing page. |
| `dashboard.py` | `GET /dashboard`, `GET /api/dashboard/trend`, `GET /api/dashboard/waste-by-resource`, `GET /api/metrics` | Owns `fetch_waste_trend()` / `fetch_waste_by_resource()` — the HTML page and its two AJAX endpoints share the same query logic so the chart and the server-rendered page never disagree. |
| `recommendations.py` | `GET /recommendations`, `GET /api/recommendations`, `POST /api/recommendations/{id}/ask`, `POST /api/actions` | The biggest one: `/api/actions` handles approve/reject/dismiss/cancel/execute, including grace-period scheduling and Terraform-PR routing. Imports `_execute_ec2_boto3` from `ui/jobs.py` (shared with the grace-period executor job). |
| `history.py` | `GET /history` | Action audit trail. |
| `reports.py` | `GET /reports`, `GET /api/reports/download`, `POST /api/reports/narrative`, `GET /api/briefing/today` | Owns `_resolve_report_period()`, shared by all three report routes. |
| `logs.py` | `GET /logs`, `GET /api/logs` | In-memory debug log viewer. |
| `settings.py` | `GET /settings`, `POST /api/config`, `GET/POST /api/policies/{export,import}`, `POST /api/whitelist` | Config editing, policy-as-code, whitelist. |
| `cloud_resources.py` | `GET /cloud-resources` | Live boto3 inventory across `CLOUD_REGIONS`, fetched in parallel via `ThreadPoolExecutor`. |
| `sync.py` | `POST /api/sync-aws` | Manual trigger for the same reconciliation `sync_aws_job` runs automatically every 5 min — imports `_sync_ec2_instance_states` from `ui/jobs.py` so both paths resolve identically. |

## Conventions

- Import shared state (`get_db`, `templates`, `_config_manager`, constants) from `ui/state.py` — never from another `routes/*` module or from `ui/main.py` (avoids circular imports).
- Import background-job helpers (`_execute_ec2_boto3`, `_sync_ec2_instance_states`) from `ui/jobs.py` when a route needs the exact same logic a scheduled job uses.
- Keep route-local helpers (e.g. `dashboard.py`'s `_llm_provider`) in the route module itself if nothing else needs them.
- A new page = a new module here + `app.include_router(...)` added in `ui/main.py`.
