# Wasteless UI

FastAPI web dashboard for [Wasteless](../README.md). Lives in the main
repository under `ui/` and runs in its **own virtualenv**, separate from the
backend pipeline.

---

## Quick Start

From the repository root (PostgreSQL must be running — `docker-compose up -d postgres`):

```bash
cd ui
./install.sh    # creates ui/venv and installs dependencies
./start.sh      # starts the UI
```

Open http://localhost:8888

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     ui/  (this app)                     │
│              FastAPI + Jinja2  :8888                    │
│        APScheduler: AWS state sync every 5 min          │
└──────────────────────┬──────────────────────────────────┘
                       │ psycopg2
┌──────────────────────▼──────────────────────────────────┐
│                     PostgreSQL                          │
│    waste_detected · recommendations · actions_log       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               Backend pipeline (../src/)                │
│      Collects metrics · detects waste · remediates      │
└──────────────────────┬──────────────────────────────────┘
                       │ boto3
                  AWS CloudWatch / EC2
```

The UI imports the backend remediator by injecting the repository root into
`sys.path` at runtime (see `utils/remediator.py`).

---

## Configuration

Copy and edit `ui/.env`:

```bash
cp .env.template .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `wasteless` | Database name |
| `DB_USER` | `wasteless` | Database user |
| `DB_PASSWORD` | *(required)* | Database password |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

Use the same database credentials as the root `.env`. AWS credentials are
read from the environment or `~/.aws/credentials`.

---

## Features

| Feature | Description |
|---------|-------------|
| **Dashboard** | KPIs, savings metrics, recommendations overview |
| **Recommendations** | Confidence-scored actions with AI insights, approve/reject |
| **Dry-Run Mode** | Test actions safely before production |
| **Auto-Sync** | Background sync every 5 min with AWS (APScheduler) |
| **Manual Sync** | On-demand sync via UI or API |
| **Action History** | Full audit trail of remediations |
| **Cloud Inventory** | Live EC2 inventory across regions |
| **Whitelist** | Exclude instances from recommendations |

---

## Routes

See [routes/README.md](routes/README.md) for which module owns each endpoint.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Home overview |
| `/landing` | GET | Public landing page |
| `/dashboard` | GET | KPIs + waste trend/by-resource charts |
| `/api/dashboard/trend` | GET | JSON waste trend series |
| `/api/dashboard/waste-by-resource` | GET | JSON waste breakdown by resource type |
| `/recommendations` | GET | Pending recommendations |
| `/api/recommendations` | GET | JSON recommendations |
| `/api/recommendations/{id}/ask` | POST | Ask the AI about a recommendation |
| `/history` | GET | Action history |
| `/reports` | GET | Activity report |
| `/api/reports/download` | GET | Download report as Markdown |
| `/api/reports/narrative` | POST | AI summary of a report |
| `/api/briefing/today` | GET | Daily AI briefing |
| `/logs` | GET | Live log viewer (debug) |
| `/api/logs` | GET | Incremental log poll |
| `/settings` | GET | Configuration |
| `/cloud-resources` | GET | EC2/EBS/EIP/VPC/snapshot/S3 inventory |
| `/api/metrics` | GET | JSON metrics |
| `/api/actions` | POST | Approve / reject / dismiss / cancel |
| `/api/config` | POST | Update config (dry_run…) |
| `/api/policies/export` | GET | Download policy as YAML |
| `/api/policies/import` | POST | Validate and apply a policy YAML |
| `/api/whitelist` | POST | Add/remove from whitelist |
| `/api/sync-aws` | POST | Trigger manual sync |

---

## Development

```bash
# Hot reload (from ui/, with ui/venv activated)
uvicorn main:app --reload --port 8888

# Tests
python run_tests.py
```

---

## Structure

```
ui/
├── main.py              # Assembles the app: create FastAPI, mount static, include routers
├── state.py             # Shared app state: DB config, templates, scheduler, config manager
├── jobs.py              # APScheduler background jobs (sync/grace/terraform-pr, every 5 min)
├── schemas.py           # Pydantic request models
├── routes/              # One APIRouter per page/domain — see routes/README.md
├── templates/           # Jinja2 HTML templates — see templates/README.md
├── static/              # CSS / JS assets — see static/README.md
├── utils/               # config_manager, remediator, aws_clients, aws_sync, action_registry, policies, terraform_pr, reports, log_buffer, logger
├── tests/               # UI tests (python run_tests.py) — see tests/README.md
├── scripts/             # Benchmark/cleanup/integration scripts — see scripts/README.md
├── install.sh           # Setup script (creates ui/venv)
├── start.sh             # Start script
└── requirements.txt     # UI-specific dependencies
```

---

## License

Apache 2.0 — see the [main repository](../README.md).
