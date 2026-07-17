# Development Guide

> Local development, testing, and common tasks for Wasteless contributors.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | `brew install python@3.11` (Mac) |
| Docker + Compose | 24.0+ / 2.0+ | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| Git | 2.30+ | |
| AWS CLI | 2.x | Optional, useful for debugging credentials |
| Steampipe | latest | Optional — required for the Steampipe-based detectors |

---

## Two Python environments

The project uses **two separate virtualenvs**:

- **`venv/`** (root) — backend pipeline (`src/`): collectors, detectors, remediators, tests
- **`ui/venv/`** — FastAPI web UI (`ui/`)

Runtime dependencies are pinned in `requirements.lock` and
`ui/requirements.lock`; development tools are pinned in
`requirements-dev.lock`. The backend package is installed in editable mode in
both environments so the UI can import backend modules without a path hack.

---

## Setup

```bash
git clone https://github.com/wasteless-io/wasteless.git
cd wasteless
./install.sh --no-schedule
```

The root installer creates both virtual environments from the lock files,
installs the backend in editable mode, starts PostgreSQL and seeds local
configuration. AWS can remain disconnected for unit development.

Verify the environment:

```bash
./install.sh --doctor
make test
```

---

## Project structure

```
wasteless/
├── src/
│   ├── aws_collector.py       # Cost Explorer → cloud_costs_raw
│   ├── collectors/            # CloudWatch + Steampipe collection
│   │   ├── aws_cloudwatch.py  # CloudWatch metrics → ec2_metrics
│   │   └── steampipe.py       # Steampipe query layer
│   ├── detectors/             # Waste detection rules
│   │   ├── ec2_idle.py        # avg CPU < 5% over 7 days
│   │   ├── ebs_orphan.py, eip_orphan.py, elb_unused.py,
│   │   ├── nat_gateway_unused.py, snapshot_orphan.py, ami_orphan.py,
│   │   ├── rds_idle.py, rds_stopped.py, rds_snapshot_orphan.py, ...
│   │   └── steampipe_base.py  # Base class for Steampipe detectors
│   ├── remediators/           # Stop / terminate / delete execution
│   ├── trackers/              # savings_tracker.py (Cost Explorer verification)
│   ├── core/                  # database, config, safeguards, llm (AI insights)
│   └── utils/                 # Helpers, cleanup scripts
├── ui/                        # FastAPI web dashboard (own venv)
├── sql/
│   ├── init.sql               # Initial schema
│   ├── steampipe/             # SQL queries used by Steampipe detectors
│   └── migrations/            # Schema changes
├── config/remediation.yaml    # Safeguard policies
├── scripts/                   # Automation (cron install, run_* wrappers)
├── terraform/test-fixtures/   # Billed AWS fixtures to validate detectors
└── tests/                     # pytest (unit/ + end-to-end)
```

---

## Workflow

Branches are created from `dev` (see [CONTRIBUTING](../CONTRIBUTING.md) for details):

```bash
git checkout dev && git pull
git checkout -b feature/your-feature
```

Commit messages follow `type: description` (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

Before pushing:

```bash
make lint
make test
make test-ui
```

---

## Testing

```bash
# Backend (root venv)
make test
./venv/bin/pytest tests/unit/test_safeguards.py
./venv/bin/pytest -k "test_confidence"

# UI
make test-ui
```

Detectors can also be validated against **real AWS resources** using the Terraform
test fixtures — see [terraform/test-fixtures/README.md](../terraform/test-fixtures/README.md).
Always `terraform destroy` afterwards.

### What runs where

Skips are intentional, not failures — `pytest -rs` (on by default) tells you
what was skipped and why. The map:

| Test layer | Needs | Locally | Per-PR CI | Nightly |
|---|---|---|---|---|
| Unit (backend + UI) | nothing | ✅ always | ✅ | ✅ |
| DB integration | PostgreSQL | ✅ (`make test` starts it) | ✅ (service container) | ✅ |
| AWS write path | nothing (moto, in-process) | ✅ | ✅ | ✅ |
| backup/restore | docker or psql CLI | if installed | ✅ | ✅ |
| terraform editor | terraform CLI | if installed | skipped | skipped |
| Live AWS | sandbox account creds | never | never (credential-free) | gated step |

---

## Adding a new detector

Two patterns exist:

**1. Metrics-based** (like `ec2_idle.py`): query collected metrics in PostgreSQL,
compute a confidence score, insert into `waste_detected` then `recommendations`.

**2. Steampipe-based** (preferred for new inventory-style detectors): write the
detection SQL in `sql/steampipe/your_detector.sql` and subclass
`SteampipeWasteDetector` (`steampipe_base.py`) — only `map_rows()` is needed.
See `vpc_unused.py` for a minimal example.

In both cases:

0. **Don't duplicate an existing detector's resource type in the other
   pattern.** `ebs_orphan`, `eip_orphan` and `snapshot_orphan` used to have
   both a boto3 and a Steampipe implementation; the duplicates were removed
   because nothing enforced they'd stay in sync. Pick boto3 or Steampipe
   once per resource type.

1. **Declare the `recommendation_type` in `ui/utils/action_registry.py`** with
   an execution mode (`boto3`, `remediator` or `manual`) — the guard test in
   `ui/tests/test_action_registry.py` fails on undeclared types
2. Add a migration in `sql/migrations/` if new tables are needed
3. Add unit tests in `tests/unit/`
4. Add the detector to `wasteless.sh collect` and update
   `src/detectors/README.md` plus `docs/ARCHITECTURE.md`
5. Add a Terraform test fixture if the resource can be fabricated cheaply
6. Update the README capability summary if the supported service scope changes

---

## Database management

```bash
# psql access
docker exec -it wasteless-postgres psql -U wasteless -d wasteless

# Apply a migration
docker exec -i wasteless-postgres psql -U wasteless -d wasteless < sql/migrations/your_migration.sql

# Backup / restore
docker exec wasteless-postgres pg_dump -U wasteless wasteless > backup_$(date +%Y%m%d).sql
docker exec -i wasteless-postgres psql -U wasteless -d wasteless < backup_20260101.sql

# Full reset (WARNING: deletes all data; init.sql re-runs on startup)
docker compose down -v && docker compose up -d postgres
```

Useful queries:

```sql
-- Total waste by type (last 30 days)
SELECT waste_type, COUNT(*), SUM(monthly_waste_eur) AS total
FROM waste_detected
WHERE detection_date >= CURRENT_DATE - 30
GROUP BY waste_type ORDER BY total DESC;

-- Pending recommendations
SELECT r.id, r.recommendation_type, r.action_required, r.estimated_monthly_savings_eur
FROM recommendations r WHERE r.status = 'pending';
```

---

## Debugging

- **Logging**: modules use the project logger (`src/core/`); run scripts directly to see console output.
- **Breakpoints**: `breakpoint()` anywhere, then standard pdb (`n`, `s`, `c`, `p var`, `q`).
- **UI hot reload**: `cd ui && uvicorn main:app --reload --port 8888`.

Common issues:

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError` | Wrong venv — check `which python` points into the right `venv/` |
| DB connection refused | `docker compose ps`, then `docker compose logs postgres` |
| Port 5432/8888 in use | `lsof -i :5432`, kill the process or change the port |
| AWS `InvalidClientTokenId` | Check `.env` credentials, test with `aws sts get-caller-identity` |

---

## Pre-PR checklist

- [ ] Code formatted with Black, no Ruff errors
- [ ] All tests pass (`pytest` + `cd ui && python run_tests.py`)
- [ ] New features have tests
- [ ] Documentation updated
- [ ] `.env` not committed, no hardcoded credentials
- [ ] **Narrow the excepts you touch**: existing broad `except Exception`
      handlers at I/O boundaries are tolerated, but any handler you edit
      (and any new one) should catch the concrete exceptions instead
      (`botocore.exceptions.ClientError`, `psycopg2.Error`, …) and always
      log what it swallows
