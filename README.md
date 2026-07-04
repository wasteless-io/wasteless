# Wasteless

Open-source cloud cost optimization. Detect idle and orphaned AWS resources. Remediate with one click.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-orange.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)

---

## Quick Start

**Requirements:** Docker, Python 3.11+, AWS credentials (`aws configure`)

On macOS, install all prerequisites in one command:

```bash
brew bundle           # Installs Python, Docker Desktop, uv, AWS CLI (see Brewfile)
```

On Windows, use WSL2 — the native path is not supported (see [Windows setup](#windows-wsl2) below).

```bash
git clone https://github.com/wastelessio/wasteless.git
cd wasteless
./install.sh          # Installs everything (backend + UI + DB)

source ~/.zshrc
wasteless             # Start the web UI
```

Open http://localhost:8888

Then collect data and detect waste:

```bash
source venv/bin/activate
python3 src/collectors/aws_cloudwatch.py   # Collect metrics
python3 src/detectors/ec2_idle.py          # Detect waste
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AWS Account                          │
│         CloudWatch API  ·  Cost Explorer  ·  EC2        │
└──────────────────────┬──────────────────────────────────┘
                       │ boto3
┌──────────────────────▼──────────────────────────────────┐
│                     wasteless                           │
│                                                         │
│  collectors/    →   CloudWatch metrics + Steampipe      │
│  detectors/     →   Identify idle / orphaned resources  │
│  remediators/   →   Stop / release / delete (guarded)   │
│  trackers/      →   Verify actual savings               │
└──────────────────────┬──────────────────────────────────┘
                       │ psycopg2
┌──────────────────────▼──────────────────────────────────┐
│                     PostgreSQL                          │
│    ec2_metrics · waste_detected · recommendations       │
│    actions_log · rollback_snapshots · savings_realized  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                      ui/  (FastAPI)                     │
│         Dashboard · Recommendations · History           │
│                    :8888                                │
└─────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
wasteless/
├── src/
│   ├── collectors/         # CloudWatch + Steampipe collection
│   ├── detectors/          # Waste detection (EC2, EBS, EIP, ELB, NAT, snapshots)
│   ├── remediators/        # Stop / release / delete execution
│   ├── trackers/           # Savings verification
│   └── core/               # Database, safeguards, AI insights (llm)
├── ui/                     # FastAPI web dashboard
│   ├── main.py
│   ├── templates/
│   ├── utils/
│   ├── install.sh
│   └── start.sh
├── sql/                    # Database schema + migrations
├── config/
│   └── remediation.yaml   # Safeguards and policies
├── docker-compose.yml      # PostgreSQL (+ Metabase optionnel)
└── requirements.txt
```

---

## Configuration

### Environment variables

Copy `.env.template` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `AWS_ACCESS_KEY_ID` | *(required)* | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | *(required)* | AWS secret key |
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `wasteless` | Database name |
| `DB_USER` | `wasteless` | Database user |
| `DB_PASSWORD` | *(required)* | Database password |

### Remediation policy

Edit `config/remediation.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `auto_remediation.enabled` | `false` | Enable autonomous execution |
| `auto_remediation.dry_run_days` | `7` | Mandatory dry-run period |
| `approval.grace_period_days` | `3` | Delay between approval and execution (0 = immediate, cancellable meanwhile) |
| `protection.min_instance_age_days` | `30` | Ignore instances younger than N days |
| `protection.min_idle_days` | `14` | Must be idle for N+ days |
| `protection.min_confidence_score` | `0.80` | Minimum detection confidence |
| `protection.max_instances_per_run` | `3` | Max instances stopped per run |

Whitelist instances by ID or tag:

```yaml
whitelist:
  instance_ids:
    - i-0123456789abcdef0
  tags:
    Environment: production
```

### AWS IAM permissions

Minimum required policy:

```json
{
  "Action": [
    "cloudwatch:GetMetricStatistics",
    "cloudwatch:ListMetrics",
    "ec2:Describe*",
    "ec2:StopInstances",
    "ce:GetCostAndUsage"
  ]
}
```

---

## Safeguards

Before executing any action, Wasteless validates 7 conditions:

1. Auto-remediation enabled in config
2. Instance not in whitelist
3. Instance age >= 30 days
4. Detection confidence >= 80%
5. Idle duration >= 14 consecutive days
6. Current time in allowed schedule window
7. Instances stopped this run < max limit

**If any check fails → action aborted and logged.**

On top of the 7 checks, approvals can go through a **grace period**
(`approval.grace_period_days`): the action is scheduled instead of executed,
stays cancellable from the Recommendations page, and runs automatically once
the delay elapses. The whole policy is exportable/importable as YAML
(Settings → Policy as Code) so it can be versioned and reviewed in git.

---

## Features

| Feature | Description |
|---------|-------------|
| **Detection** | Idle EC2 (CPU/network analysis) + orphaned EBS, EIP, ELB, NAT gateways, snapshots, gp2 volumes — with confidence scoring |
| **Recommendations** | Stop / terminate / release / delete / downsize actions |
| **AI Insights** | LLM-generated context per recommendation (provider-agnostic via litellm) |
| **Dry-Run Mode** | Test safely before any AWS action |
| **Auto-Sync** | Background sync every 5 min with AWS |
| **Action History** | Full audit trail with rollback snapshots |
| **Cloud Inventory** | Live EC2 inventory across regions |
| **Whitelist** | Exclude instances from recommendations |
| **Savings Tracking** | Verified actual savings via Cost Explorer |

---

## API (UI)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Home dashboard |
| `/recommendations` | GET | Pending recommendations |
| `/history` | GET | Action history |
| `/reports` | GET | Activity report over a date range |
| `/logs` | GET | Live log viewer with search (debug) |
| `/cloud-resources` | GET | EC2 inventory |
| `/settings` | GET | Configuration |
| `/api/metrics` | GET | JSON metrics |
| `/api/actions` | POST | Approve / reject |
| `/api/config` | POST | Update config |
| `/api/whitelist` | POST | Add to whitelist |
| `/api/sync-aws` | POST | Trigger manual sync |
| `/api/reports/download` | GET | Download a report as Markdown |
| `/api/reports/narrative` | POST | AI summary of a report (on demand) |
| `/api/logs` | GET | Incremental poll of in-memory logs |
| `/api/policies/export` | GET | Download the remediation policy as YAML |
| `/api/policies/import` | POST | Validate and apply a policy YAML |

---

## Compatibility

| OS | Status |
|----|--------|
| macOS | Supported |
| Linux | Supported |
| Windows (WSL2) | Supported |
| Windows (native) | Not supported |

### Windows (WSL2)

Wasteless runs on Windows through WSL2. One-time setup:

1. Install WSL2 with Ubuntu (PowerShell as administrator, then reboot):

   ```powershell
   wsl --install -d Ubuntu
   ```

2. Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
   with the **WSL2 backend**, then enable the Ubuntu integration in
   *Settings → Resources → WSL integration*.

3. Inside the Ubuntu terminal, install the prerequisites:

   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip git unzip
   ```

4. Clone the repository **in the Linux filesystem** (e.g. `~/`), **not** under
   `/mnt/c/...` — the Windows filesystem breaks permissions and is 10-50x
   slower from WSL:

   ```bash
   cd ~
   git clone https://github.com/wastelessio/wasteless.git
   cd wasteless
   ./install.sh
   ```

Everything else (Quick Start, tests, UI) works exactly as on Linux.

---

## Development

```bash
# Backend tests (root venv)
source venv/bin/activate
pytest

# UI hot reload
cd ui && uvicorn main:app --reload --port 8888

# UI tests
cd ui && python run_tests.py
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full development guide
and [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

### Adding a new detector

1. Write the Steampipe query in `sql/steampipe/<name>.sql`.
2. Subclass `SteampipeWasteDetector` in `src/detectors/<name>.py` — only
   `map_rows()` is needed (see `vpc_unused.py` for a minimal example).
3. **Declare the `recommendation_type` in
   `ui/utils/action_registry.py`** with a conscious execution mode:
   `boto3` (direct EC2 automation), `remediator` (backend safeguards
   pipeline) or `manual` (approval records the decision, execution stays
   human). The guard test in `ui/tests/test_action_registry.py` fails on
   undeclared types.
4. Add `map_rows()` unit tests in `tests/unit/test_steampipe_detectors.py`.

---

## Roadmap

- [x] EC2 idle detection and remediation
- [x] EBS / EIP / ELB / NAT gateway / snapshot detection (Steampipe)
- [x] Web dashboard (FastAPI)
- [x] Dry-run mode and safeguards
- [x] Savings verification
- [x] AI insights per recommendation
- [ ] RDS / S3 detection
- [ ] Multi-account AWS support
- [ ] Slack / Teams notifications
- [ ] Azure and GCP support

---

## License

Apache 2.0

---

## Links

- **Issues**: [GitHub Issues](https://github.com/wastelessio/wasteless/issues)
- **Contact**: wasteless.io.entreprise@gmail.com
