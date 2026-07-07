# Wasteless — Architecture

> Technical architecture and design decisions for the cloud waste elimination platform.

---

## System overview

```
┌──────────────────────────────────────────────────────────┐
│                       AWS Account                        │
│   CloudWatch API · Cost Explorer · EC2 / EBS / ELB APIs  │
└──────────────────────┬───────────────────────────────────┘
                       │ boto3 / Steampipe
┌──────────────────────▼───────────────────────────────────┐
│                   Backend pipeline (src/)                │
│                                                          │
│  collectors/   CloudWatch metrics + Steampipe inventory  │
│  aws_collector Cost Explorer costs                       │
│  detectors/    8 waste detection rules                   │
│  remediators/  Stop / release / delete (safeguarded)     │
│  trackers/     Verified savings via Cost Explorer        │
│  core/         database · config · safeguards · llm      │
└──────────────────────┬───────────────────────────────────┘
                       │ psycopg2
┌──────────────────────▼───────────────────────────────────┐
│                       PostgreSQL                         │
│   ec2_metrics · cloud_costs_raw · waste_detected         │
│   recommendations · actions_log · rollback_snapshots     │
│   savings_realized · active_waste (view)                 │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│                  Web UI (ui/, FastAPI :8888)             │
│   Dashboard · Recommendations · History · Settings       │
│   Cloud Resources · AI insights · APScheduler sync       │
└──────────────────────────────────────────────────────────┘
```

Metabase remains available as an **optional** SQL exploration layer
(`docker-compose up -d` starts it on port 3000), but the primary interface is
the FastAPI UI.

---

## Data flow

### Collection

Two collection paths feed PostgreSQL:

1. **CloudWatch** (`src/collectors/aws_cloudwatch.py`) — lists running EC2
   instances, fetches per-instance CPU/network metrics, writes daily rows to
   `ec2_metrics`.
2. **Steampipe** (`src/collectors/steampipe.py` + `sql/steampipe/*.sql`) —
   runs inventory SQL against the AWS plugin (unused NAT gateways, gp2
   volumes eligible for gp3 migration, unused ELBs, unused VPCs). Each
   detector owns one SQL file.

`src/aws_collector.py` additionally pulls Cost Explorer daily costs into
`cloud_costs_raw`.

All collectors are **idempotent** (`ON CONFLICT DO NOTHING`), batch-insert via
`execute_values`, and fail gracefully.

### Detection

Detectors live in `src/detectors/` and follow one of two patterns:

| Pattern | Base | Example |
|---------|------|---------|
| boto3-based | direct `describe_*` calls | `ec2_idle.py` (avg CPU < 5% over 7 days), `ebs_orphan.py`, `eip_orphan.py`, `snapshot_orphan.py` |
| Steampipe-based | `steampipe_base.py` + SQL file | `vpc_unused.py`, `nat_gateway_unused.py`, `elb_unused.py`, `ebs_gp2_migration.py` |

Current detectors: `ec2_idle`, `ec2_stopped`, `ebs_orphan`, `eip_orphan`,
`snapshot_orphan` (boto3); `ebs_gp2_migration`, `elb_unused`,
`nat_gateway_unused`, `vpc_unused` (Steampipe). Each resource type has
exactly one canonical detector — no boto3/Steampipe duplicates.

Each detection produces:
- a row in `waste_detected` (confidence score 0–1, estimated monthly waste in
  EUR, `metadata` JSONB with the evidence),
- a linked row in `recommendations` (status `pending`).

### Remediation

`src/remediators/` executes approved actions (stop instance, release EIP,
delete volume/snapshot…). Every action:

1. passes through the **7 safeguard checks** (see below),
2. takes a rollback snapshot when applicable (`rollback_snapshots`),
3. is written to the audit trail (`actions_log`).

All actions default to **dry-run**; auto-remediation is opt-in per action type
in `config/remediation.yaml`.

### Verification

`src/trackers/savings_tracker.py` compares actual Cost Explorer spend after an
action and records verified savings in `savings_realized`.

### AI insights

`src/core/llm.py` (litellm) generates a natural-language insight per
recommendation, stored alongside it and displayed in the UI. The provider is
configurable; the feature degrades gracefully when no API key is set.

---

## Safeguards

`src/core/safeguards.py` runs 7 sequential checks before any AWS action.
**Any failure aborts the action and logs the reason:**

1. Auto-remediation enabled in config
2. Instance not whitelisted (by ID or tag)
3. Instance age ≥ 30 days
4. Detection confidence ≥ 0.80
5. Idle duration ≥ 14 consecutive days
6. Current time within the allowed schedule window
7. Instances stopped this run < max limit

Thresholds live in `config/remediation.yaml`
(`RemediationConfig.from_yaml()` in `src/core/config.py`).

---

## Database schema

Key tables (`sql/init.sql` + `sql/migrations/`):

| Table | Purpose |
|-------|---------|
| `cloud_costs_raw` | Raw Cost Explorer data (provider, service, usage_date, cost) |
| `ec2_metrics` | Daily CloudWatch metrics per instance (CPU avg/max, network, tags JSONB) |
| `waste_detected` | Detected waste: resource, waste_type, monthly_waste_eur, confidence_score, metadata JSONB |
| `recommendations` | Actions linked to waste (status: pending / applied / rejected / obsolete) |
| `actions_log` | Audit trail of every executed action |
| `rollback_snapshots` | Pre-action state for rollback |
| `savings_realized` | Verified actual savings (Cost Explorer) |

The `active_waste` **view** is the single source of truth for waste aggregates
shown in the UI (it excludes obsolete/rejected items).

Relations: `waste_detected 1—n recommendations 1—n actions_log / savings_realized`.

---

## Web UI

FastAPI app (`ui/main.py`) with Jinja2 templates, running on port 8888 in its
**own virtualenv** (`ui/venv/`). It imports the backend remediator by injecting
the repo root into `sys.path` (`ui/utils/remediator.py`).

- **Pages**: Dashboard, Recommendations, History, Settings, Cloud Resources,
  plus a public landing page.
- **Background sync**: APScheduler checks live EC2 state every 5 minutes and
  reconciles recommendation statuses (instances stopped/terminated outside
  Wasteless are marked obsolete).
- **API routes** under `/api/`: metrics, approve/reject actions, config
  updates, whitelist, manual AWS sync.

---

## Technology decisions

| Choice | Rationale |
|--------|-----------|
| **PostgreSQL 16** | JSONB metadata, analytics performance, ACID, free. ClickHouse overkill at this scale; MongoDB lacks joins. |
| **Python 3.11+** | Mature boto3, fast development, easy to contribute to. |
| **FastAPI + Jinja2** | Lightweight server-rendered UI, no separate frontend build, easy self-hosting. Replaced the earlier Metabase-only approach. |
| **Steampipe** | SQL-over-cloud-APIs makes inventory detectors declarative: one SQL file per rule instead of bespoke boto3 pagination code. |
| **Docker Compose** | Reproducible local/VPS deployment without Kubernetes complexity. |
| **litellm** | Provider-agnostic LLM access for AI insights; no vendor lock-in. |

---

## Security

- **IAM**: read-only policy for detection; remediation actions require
  explicitly added write permissions (see [AWS_SETUP.md](AWS_SETUP.md)).
- **Secrets**: `.env` files (gitignored), boto3 credential chain supported
  (IAM roles work).
- **Blast radius**: safeguards + dry-run default + per-action-type automation
  toggles + whitelist.
- **Audit**: every action logged with rollback snapshot.
- **Network**: PostgreSQL bound to localhost; the UI is meant to sit behind a
  reverse proxy with TLS when exposed (see [DEPLOYMENT.md](DEPLOYMENT.md)).

---

## Testing strategy

- **Unit tests** (`tests/unit/`): safeguards, config, remediators, Steampipe
  collectors/detectors, LLM insights, validation.
- **End-to-end** (`tests/test_end_to_end.py`): collect → detect → verify
  against a live database.
- **Real-conditions validation**: `terraform/test-fixtures/` provisions one
  cheap billed AWS resource per detector (orphaned EIP, idle NAT gateway,
  gp2 volume, unused ALB) — apply, run detectors, verify in the UI, always
  destroy. See [terraform/test-fixtures/README.md](../terraform/test-fixtures/README.md).
- **UI tests**: `cd ui && python run_tests.py`.

---

## Roadmap

Done:
- EC2 idle/stopped detection and remediation
- EBS orphan + gp2 migration, EIP orphan, ELB unused, NAT gateway unused,
  snapshot orphan detectors (Steampipe layer)
- FastAPI dashboard, safeguards, dry-run, savings verification
- AI insights per recommendation

Planned:
- RDS / S3 detection
- Multi-account AWS support
- Slack / Teams notifications
- Azure and GCP support
