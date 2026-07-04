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

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Home dashboard |
| `/dashboard` | GET | Detailed metrics |
| `/recommendations` | GET | Pending recommendations |
| `/history` | GET | Action history |
| `/settings` | GET | Configuration |
| `/cloud-resources` | GET | EC2 inventory |
| `/landing` | GET | Public landing page |
| `/api/metrics` | GET | JSON metrics |
| `/api/recommendations` | GET | JSON recommendations |
| `/api/actions` | POST | Approve / reject |
| `/api/config` | POST | Update config (dry_run…) |
| `/api/whitelist` | POST | Add to whitelist |
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
├── main.py              # FastAPI app + all routes
├── templates/           # Jinja2 HTML templates
├── static/              # CSS / JS assets
├── utils/               # database, config_manager, remediator, pagination, sidebar
├── tests/               # UI tests (python run_tests.py)
├── install.sh           # Setup script (creates ui/venv)
├── start.sh             # Start script
└── requirements.txt     # UI-specific dependencies
```

---

## License

Apache 2.0 — see the [main repository](../README.md).
