# Wasteless

**Open-source FinOps control for AWS. Detect waste, turn evidence into controlled
actions, and verify what changed.**

[![Tests](https://github.com/wasteless-io/wasteless/actions/workflows/tests.yml/badge.svg)](https://github.com/wasteless-io/wasteless/actions/workflows/tests.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)

Wasteless is self-hosted. It collects AWS telemetry through a read-only role,
scores waste, proposes an action path, and keeps the decision and outcome in an
auditable control loop.

> [!IMPORTANT]
> Wasteless is under active `0.x` development. Detection is read-only by
> default and real AWS actions are opt-in. Validate your policy and permissions
> in a non-production account before enabling writes.

## What Wasteless does

| Stage | Product capability |
|---|---|
| **Observe** | Collects CloudWatch metrics, Cost Explorer spend and AWS inventory through boto3 and Steampipe. |
| **Detect** | Finds idle or stopped EC2 and RDS instances, orphaned EBS volumes, Elastic IPs and AMIs, old snapshots, unused load balancers, NAT gateways and VPCs, plus gp2 to gp3 candidates. |
| **Prioritize** | Attaches evidence, confidence and estimated monthly cost to each recommendation. |
| **Explain** | Adds optional provider-agnostic LLM insights and a daily briefing when an AI provider is configured. |
| **Act** | Routes an approval to a direct AWS action, a guarded backend remediator, an optional Terraform PR, or a manual task according to the recommendation type and policy. |
| **Verify** | Reconciles live resource state and records the action trail. A standalone Cost Explorer tracker verifies eligible EC2 stop savings after at least seven days of billing data. |

Potential savings remain estimates until an action is completed and its outcome
can be measured.

## Safety model

- In the recommended role-based setup, boto3 collection and detection use a
  dedicated **read-only IAM role**. Steampipe must be configured separately to
  assume that role.
- In that setup, AWS writes require a separate, optional **remediation role**
  and fail closed when it is absent. Legacy default-chain credentials remain
  supported and retain whatever permissions the source identity already has.
- **Dry-run is enabled by default** and real automation is opt-in.
- Automated actions can use per-action switches and a cancellable grace period.
- Backend remediators re-check live state and policy before supported writes.
- Manual recommendations never cause an AWS write from Wasteless.
- Decisions and execution attempts are written to the action history. A
  pre-action state is stored when applicable, but not every AWS action is
  reversible.

Read [Remediation and controls](docs/REMEDIATION.md) for the exact execution
matrix and [AWS setup](docs/AWS_SETUP.md) for the IAM model.

## Quick start

### Requirements

- macOS, Linux, or Windows through WSL2
- Python 3.11 or newer
- Docker with Docker Compose
- Git

AWS CLI is optional. Steampipe is also optional, but the ELB, NAT, VPC, gp2,
AMI and RDS detectors are skipped until Steampipe and its AWS plugin are
installed.

### 1. Install

```bash
git clone https://github.com/wasteless-io/wasteless.git
cd wasteless
./install.sh
```

The installer creates both Python environments, starts PostgreSQL, configures
the local CLI, offers to install scheduled collection, and starts the web UI.
It does not require AWS to be connected yet.

Prefer an archive instead of Git? Use the
[latest published release](https://github.com/wasteless-io/wasteless/releases/latest).

### 2. Connect AWS

Open <http://localhost:8888/setup> and follow the guided CloudFormation setup.
Detection needs only the read-only role. The write role can be omitted for a
detection-only deployment.

See the [guided quick start](docs/CTO_QUICKSTART.md) or the complete
[AWS setup guide](docs/AWS_SETUP.md) when you need Terraform, ExternalId, or
manual IAM configuration.

### 3. Verify

```bash
wasteless status
```

The first collection starts after AWS setup is saved. To run a complete cycle
immediately:

```bash
wasteless collect
```

Then open <http://localhost:8888/recommendations>.

## CLI

| Command | Purpose |
|---|---|
| `wasteless` | Start the web UI in the background |
| `wasteless status` | Check the UI and scheduled collection |
| `wasteless collect` | Run collection and detection once |
| `wasteless logs` | Follow the application log |
| `wasteless stop` | Stop the web UI |
| `wasteless schedule` | Install five-minute OS-level collection |
| `wasteless unschedule` | Remove OS-level collection |

If the alias is not available in the current terminal yet, use
`./wasteless.sh <command>` or open a new terminal.

## How it works

```text
CloudWatch · Cost Explorer · AWS inventory
                    │ read-only collection
                    ▼
               PostgreSQL
                    │
                    ▼
                Detectors
                    │ evidence + recommendation
                    ▼
     Manual task · Terraform PR · Controlled AWS action
                    │
                    ▼
          Live sync · Audit · Savings verification
```

The FastAPI UI presents the same data and control loop on port `8888`. See the
[architecture guide](docs/ARCHITECTURE.md) for components, data flow and
technical decisions.

## Compatibility and current scope

| Platform | Status |
|---|---|
| macOS | Supported |
| Linux | Supported |
| Windows through WSL2 | Supported; keep the repository in the Linux filesystem |
| Native Windows | Not supported |

One installation currently manages one AWS account. Native multi-account
operation, S3, EKS, Azure and GCP are not currently supported. Follow
[GitHub issues](https://github.com/wasteless-io/wasteless/issues) for planned
work rather than relying on a static roadmap.

## Documentation

| Guide | Use it for |
|---|---|
| [Guided quick start](docs/CTO_QUICKSTART.md) | Install and connect an AWS account from the UI |
| [AWS setup](docs/AWS_SETUP.md) | IAM roles, CloudFormation, Terraform and ExternalId |
| [Remediation and controls](docs/REMEDIATION.md) | Execution modes, dry-run, approvals, rollback and policy |
| [Automation](docs/AUTOMATION_GUIDE.md) | Collection schedule, monitoring and troubleshooting |
| [Architecture](docs/ARCHITECTURE.md) | Components, data flow, storage and design decisions |
| [Deployment](docs/DEPLOYMENT.md) | VPS, TLS, authentication, backups and monitoring |
| [Development](docs/DEVELOPMENT.md) | Local workflow, tests and detector development |
| [Production validation](docs/PRODUCTION_VALIDATION.md) | Validate a real remediation against sandbox resources |

## Development

The installer can prepare a contributor environment without installing the
OS-level scheduler:

```bash
./install.sh --no-schedule
make test
make test-ui
make lint
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Security

Do not report vulnerabilities in a public issue. Follow
[SECURITY.md](SECURITY.md) for private reporting and deployment
responsibilities.

## License

Wasteless is available under the [Apache License 2.0](LICENSE).
