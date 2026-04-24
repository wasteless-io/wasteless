# Wasteless

Open-source cloud cost optimization. Detect idle EC2 instances. Stop them with one click.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-orange.svg)](https://fastapi.tiangolo.com/)

---

## Quick Start

**Requirements:** Python 3.11+, PostgreSQL, AWS credentials

```bash
git clone https://github.com/wastelessio/wasteless.git && cd wasteless
docker compose up -d

git clone https://github.com/wastelessio/wasteless-ui.git && cd wasteless-ui
./install.sh && ./start.sh
```

Open http://localhost:8888

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      wasteless-ui                        │
│              FastAPI + Jinja2  :8888                     │
└──────────────────────┬──────────────────────────────────┘
                       │ psycopg2
┌──────────────────────▼──────────────────────────────────┐
│                     PostgreSQL                           │
│              waste_records · recommendations             │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                      wasteless                           │
│         Detects idle EC2 · writes recommendations        │
└──────────────────────┬──────────────────────────────────┘
                       │ boto3
                  AWS CloudWatch / EC2
```

---

## Configuration

Copy and edit `.env`:

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
| `STREAMLIT_SERVER_PORT` | `8888` | UI port |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

AWS credentials are read from `~/.aws/credentials` (via `aws configure`).

---

## Features

| Feature | Description |
|---------|-------------|
| **Dashboard** | KPIs, savings metrics, recommendations overview |
| **Recommendations** | Confidence-scored actions, approve/reject |
| **Dry-Run Mode** | Test actions safely before production |
| **Auto-Sync** | Background sync every 5 min with AWS |
| **Manual Sync** | On-demand sync via UI or API |
| **Action History** | Full audit trail of remediations |
| **Cloud Inventory** | Live EC2 inventory across regions |
| **Whitelist** | Exclude instances from recommendations |

---

## API

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

## Compatibility

| OS | Status |
|----|--------|
| macOS | Supported |
| Linux | Supported |
| Windows (WSL2) | Supported |
| Windows (native) | Not supported |

---

## Development

```bash
# Hot reload
uvicorn main:app --reload --port 8888

# Tests
python run_tests.py
```

---

## Project Structure

```
wasteless-ui/
├── main.py              # FastAPI app + all routes
├── templates/           # Jinja2 HTML templates
├── utils/               # DB, config, remediator
├── install.sh           # Setup script
├── start.sh             # Start script
└── requirements.txt
```

---

## License

Apache 2.0

---

## Links

- **Backend**: [wastelessio/wasteless](https://github.com/wastelessio/wasteless)
- **Issues**: [GitHub Issues](https://github.com/wastelessio/wasteless-ui/issues)
- **Contact**: wasteless.io.entreprise@gmail.com
