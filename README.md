# Wasteless

Open-source cloud cost optimization. Detect idle EC2 instances. Remediate with one click.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-orange.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)

---

## Quick Start

**Requirements:** Docker, Python 3.11+, AWS credentials (`aws configure`)

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
│  collectors/    →   Fetch CloudWatch metrics            │
│  detectors/     →   Identify idle EC2 instances         │
│  remediators/   →   Stop / terminate instances          │
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
│                      ui/  (FastAPI)                      │
│         Dashboard · Recommendations · History            │
│                    :8888                                 │
└─────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
wasteless/
├── src/
│   ├── collectors/         # CloudWatch metrics collection
│   ├── detectors/          # EC2 idle waste detection
│   ├── remediators/        # Stop / terminate execution
│   ├── trackers/           # Savings verification
│   └── core/               # Database, safeguards
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

---

## Features

| Feature | Description |
|---------|-------------|
| **Detection** | CPU / network analysis with confidence scoring |
| **Recommendations** | Stop / terminate / downsize actions |
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
| `/cloud-resources` | GET | EC2 inventory |
| `/settings` | GET | Configuration |
| `/api/metrics` | GET | JSON metrics |
| `/api/actions` | POST | Approve / reject |
| `/api/config` | POST | Update config |
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
# Backend tests
python tests/test_end_to_end.py

# UI hot reload
cd ui && ./start.sh

# UI tests
cd ui && python run_tests.py
```

---

## Roadmap

- [x] EC2 idle detection and remediation
- [x] Web dashboard (FastAPI)
- [x] Dry-run mode and safeguards
- [x] Savings verification
- [ ] RDS / EBS / S3 detection
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
