# ui/templates/

Jinja2 templates rendered by the routers in [`ui/routes/`](../routes/README.md)
(no per-page Python modules). All extend `base.html` except `landing.html`.

| Template | Route | Purpose |
|---|---|---|
| `base.html` | — | Shared layout: nav, sidebar, `{% block title %}` / `{% block content %}`. Every other template (except `landing.html`) extends this. |
| `landing.html` | `/landing` | Standalone public landing page — does not extend `base.html` (no app chrome). |
| `index.html` | `/` | Home overview — KPI summary, recent events, on-demand daily briefing. |
| `dashboard.html` | `/dashboard` | KPIs, waste trend chart, waste-by-resource chart (Chart.js, see `ui/static/chart.umd.min.js`), fed by `/api/dashboard/*`. |
| `recommendations.html` | `/recommendations` | Pending recommendations — approve/reject/dismiss/cancel actions, drives `POST /api/actions`. |
| `history.html` | `/history` | Past actions, filterable by status. |
| `reports.html` | `/reports` | Activity report over a date range, download + on-demand AI narrative (`/api/reports/*`). |
| `cloud_resources.html` | `/cloud-resources` | Live EC2/EBS/EIP/VPC/snapshot/S3 inventory across regions. |
| `settings.html` | `/settings` | Config editing, whitelist, Policy-as-Code export/import. |
| `logs.html` | `/logs` | Debug log viewer, polls `/api/logs`. |

Client-side JS is inlined per-template (no separate `.js` files besides the
vendored Chart.js) — search the template itself for `<script>` when tracing
a page's behavior.
