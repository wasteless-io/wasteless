# Wasteless — Architecture

> Technical architecture and design decisions for the cloud waste elimination platform.

---

## System overview

```
┌──────────────────────────────────────────────────────────┐
│                       AWS Account                        │
│   CloudWatch · Cost Explorer · EC2 / EBS / ELB / RDS APIs│
└──────────────────────┬───────────────────────────────────┘
                       │ boto3 / Steampipe
┌──────────────────────▼───────────────────────────────────┐
│                   Backend pipeline (src/)                │
│                                                          │
│  collectors/   CloudWatch metrics + Steampipe inventory  │
│  aws_collector Cost Explorer costs                       │
│  detectors/    13 waste detection rules                  │
│  remediators/  Controlled write paths                    │
│  trackers/     Eligible EC2 stop verification            │
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
   runs inventory SQL against the AWS plugin for ELB, NAT, VPC, gp2, AMI and
   RDS detection. Each detector owns one SQL file.

`src/aws_collector.py` additionally pulls Cost Explorer daily costs into
`cloud_costs_raw`.

All collectors are **idempotent** (`ON CONFLICT DO NOTHING`), batch-insert via
`execute_values`, and fail gracefully.

### Detection

Detectors live in `src/detectors/` and follow one of two patterns:

| Pattern | Base | Example |
|---------|------|---------|
| boto3-based | direct `describe_*` calls | `ec2_idle.py`, `ec2_stopped.py`, `ebs_orphan.py`, `eip_orphan.py`, `snapshot_orphan.py` |
| Steampipe-based | `steampipe_base.py` + SQL file | `vpc_unused.py`, `ami_orphan.py`, `rds_idle.py`, `rds_stopped.py`, `rds_snapshot_orphan.py` |

Current detectors: `ec2_idle`, `ec2_stopped`, `ebs_orphan`, `eip_orphan`,
`snapshot_orphan` (boto3); `ebs_gp2_migration`, `elb_unused`,
`nat_gateway_unused`, `vpc_unused`, `ami_orphan`, `rds_idle`, `rds_stopped`
and `rds_snapshot_orphan` (Steampipe). Each rule has one canonical
implementation — no boto3/Steampipe duplicates.

Each detection produces:
- a row in `waste_detected` (confidence score 0–1, estimated monthly waste in
  EUR, `metadata` JSONB with the evidence),
- a linked row in `recommendations` (status `pending`).

### Remediation

The action registry routes each approved recommendation to one of three modes:
direct EC2 execution through boto3, a backend remediator, or a manual task that
never writes to AWS. Eligible recommendations can instead open a Terraform PR
when GitOps routing is configured.

Dry-run is enabled by default. The UI applies server-side dry-run, per-action
switches and an optional grace period. Backend remediators additionally
re-fetch live state and enforce the controls supported for that resource. All
decisions are recorded; pre-action state is stored when applicable, but not
every AWS operation is reversible. See [REMEDIATION.md](REMEDIATION.md).

### Verification

`src/trackers/savings_tracker.py` compares Cost Explorer spend for successful
EC2 stop actions and records verified savings in `savings_realized` after at
least seven days. It is currently a standalone component rather than a step in
`wasteless collect`; run it explicitly or schedule it separately.

### AI insights

`src/core/llm.py` (litellm) generates a natural-language insight per
recommendation, stored alongside it and displayed in the UI. The provider is
configurable; the feature degrades gracefully when no API key is set.

---

## Remediation controls

Controls are layered rather than described as one universal checklist:

1. detection uses a read-only IAM role;
2. writes require a separate remediation role;
3. dry-run is enabled by default;
4. automated actions can be disabled individually;
5. a grace period can delay and cancel real execution;
6. backend remediators add live-state, whitelist, confidence, schedule and
   global automation checks;
7. EC2 safeguard flows additionally enforce age, idle-duration and per-run
   limits.

Thresholds live in `config/remediation.yaml`. The exact mode and recovery
characteristics of every action are documented in
[REMEDIATION.md](REMEDIATION.md).

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
| `savings_realized` | Cost Explorer verification for eligible EC2 stop actions |

The `active_waste` **view** is the single source of truth for waste aggregates
shown in the UI (it excludes obsolete/rejected items).

Relations: `waste_detected 1—n recommendations 1—n actions_log / savings_realized`.

---

## Web UI

FastAPI app (`ui/main.py`) with Jinja2 templates, running on port 8888 in its
**own virtualenv** (`ui/venv/`). The backend package is installed in editable
mode in that environment so the UI can import the remediators directly.

- **Pages**: Dashboard, Recommendations, History, Settings, Cloud Resources,
  plus a public landing page.
- **Background jobs**: APScheduler reconciles live resource state, executes
  expired grace-period actions, tracks Terraform PRs and refreshes Cost
  Explorer data.
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

The non-obvious decisions (sync routes + connection pool, one detector per
resource, auto-remediation off by default, the two-venv split, scoped CI gates)
are recorded as lightweight ADRs in [`adr/`](adr/).

---

## Security

- **IAM**: the recommended role-based boto3 path separates read-only detection
  from write permissions; Steampipe has its own connection and legacy mode
  inherits the default credential chain (see [AWS_SETUP.md](AWS_SETUP.md)).
- **Secrets**: `.env` files (gitignored), boto3 credential chain supported
  (IAM roles work).
- **Blast radius**: dry-run default, per-action automation toggles, grace
  period, whitelist and resource-specific backend controls.
- **Audit**: decisions and execution attempts are logged; pre-action state is
  retained when the execution path supports it.
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

## Current scope

The current pipeline covers EC2, EBS, EIP, ELB, NAT gateway, VPC, AMI and RDS
recommendations. One installation manages one AWS account. S3, EKS, native
multi-account operation, Azure and GCP are outside the current scope. Track
future work through [GitHub issues](https://github.com/wasteless-io/wasteless/issues)
instead of treating this document as a release roadmap.
