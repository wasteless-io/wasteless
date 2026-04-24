# Wasteless - Architecture Documentation

> **Technical architecture and design decisions for the cloud waste elimination platform**

Version: 1.0  
Last Updated: December 2025  
Status: MVP (Phase 1)

---

## 📐 High-Level Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Cloud Providers                           │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │     AWS     │  │     GCP     │  │    Azure    │            │
│  │             │  │  (Phase 2)  │  │  (Phase 2)  │            │
│  └──────┬──────┘  └─────────────┘  └─────────────┘            │
└─────────┼────────────────────────────────────────────────────────┘
          │
          │ APIs (Cost Explorer, CloudWatch, etc.)
          │
          ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Wasteless Platform (Local)                    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                    Collectors Layer                     │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │    │
│  │  │  AWS Costs  │  │  CloudWatch  │  │  EC2 Pricing │  │    │
│  │  │  Collector  │  │  Collector   │  │   Resolver   │  │    │
│  │  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  │    │
│  └─────────┼────────────────┼──────────────────┼──────────┘    │
│            │                │                  │                │
│            ▼                ▼                  ▼                │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                  PostgreSQL Database                    │    │
│  │                                                         │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │    │
│  │  │ cloud_costs │  │ ec2_metrics │  │waste_detected│   │    │
│  │  │    _raw     │  │             │  │              │   │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │    │
│  └────────────────────┬───────────────────────────────────┘    │
│                       │                                         │
│  ┌────────────────────┼───────────────────────────────────┐    │
│  │          Detection & Analysis Layer                     │    │
│  │                    │                                    │    │
│  │  ┌─────────────────▼──────┐  ┌──────────────────┐     │    │
│  │  │   Waste Detectors      │  │  Recommendation  │     │    │
│  │  │                        │  │     Engine       │     │    │
│  │  │  • EC2 Idle           │  │                  │     │    │
│  │  │  • RDS Idle (Phase 2) │  │  • Stop/Resize   │     │    │
│  │  │  • EBS Orphan (P2)    │  │  • Scheduling    │     │    │
│  │  │  • K8s Over (P2)      │  │  • Savings calc  │     │    │
│  │  └────────────────────────┘  └──────────────────┘     │    │
│  └──────────────────────┬──────────────────────────────────┘    │
│                         │                                       │
│                         ▼                                       │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              Visualization Layer                        │    │
│  │                                                         │    │
│  │  ┌──────────────────────────────────────────────┐     │    │
│  │  │            Metabase (OSS)                     │     │    │
│  │  │                                               │     │    │
│  │  │  • AWS Cost Overview Dashboard               │     │    │
│  │  │  • Waste Detection Dashboard                 │     │    │
│  │  │  • Savings Realized Dashboard                │     │    │
│  │  │  • SQL Query Interface                       │     │    │
│  │  └──────────────────────────────────────────────┘     │    │
│  └────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Data Flow

### Collection Flow (Daily)

```
1. Scheduler (cron) triggers collectors
   ↓
2. AWS Cost Collector
   • Calls Cost Explorer API
   • Fetches last 30 days of costs
   • Groups by SERVICE
   • Granularity: DAILY
   ↓
3. PostgreSQL Insertion
   • Table: cloud_costs_raw
   • Batch insert via execute_values
   • ON CONFLICT DO NOTHING
   ↓
4. CloudWatch Collector
   • Lists running EC2 instances
   • Fetches CPU metrics (7 days)
   • Calculates avg/max CPU
   ↓
5. PostgreSQL Insertion
   • Table: ec2_metrics
   • Per-instance metrics
```

### Detection Flow (Daily/On-Demand)

```
1. Waste Detector triggered
   ↓
2. SQL Analysis
   • Query ec2_metrics
   • Calculate 7-day avg CPU
   • Filter: avg CPU < 5%
   ↓
3. Waste Calculation
   • Get instance pricing (API or static)
   • Calculate monthly_waste = cost × 0.95
   • Confidence score = 1.0 - (cpu_avg/5.0)
   ↓
4. Store Results
   • Table: waste_detected
   • Include metadata (JSON)
   ↓
5. Generate Recommendations
   • Action: "Stop instance i-xxx"
   • Estimated savings
   • Implementation effort
   ↓
6. Store Recommendations
   • Table: recommendations
   • Status: pending
```

### Visualization Flow (Real-time)

```
User opens Metabase
   ↓
Metabase queries PostgreSQL
   ↓
SQL queries aggregate data
   • SUM(cost) for total
   • GROUP BY service for breakdown
   • JOIN waste + recommendations
   ↓
Render dashboards
   • Charts update
   • Tables display
   • Metrics calculate
```

---

## 🗄️ Database Schema

### Entity Relationship Diagram

```
┌─────────────────────┐
│  cloud_costs_raw    │
│─────────────────────│
│ id (PK)             │
│ provider            │
│ account_id          │
│ service             │
│ resource_id         │
│ usage_date          │
│ cost                │
│ currency            │
│ region              │
│ raw_data (JSONB)    │
│ created_at          │
└─────────────────────┘

┌─────────────────────┐
│   ec2_metrics       │
│─────────────────────│
│ id (PK)             │
│ instance_id         │
│ instance_type       │
│ instance_state      │
│ metric_date         │
│ avg_cpu_percent     │
│ max_cpu_percent     │
│ avg_network_in_mb   │
│ tags (JSONB)        │
│ created_at          │
└──────────┬──────────┘
           │
           │
           ▼
┌─────────────────────┐
│  waste_detected     │
│─────────────────────│
│ id (PK)             │
│ detection_date      │
│ provider            │
│ account_id          │
│ resource_id         │◄──────┐
│ resource_type       │       │
│ waste_type          │       │
│ monthly_waste_eur   │       │
│ confidence_score    │       │
│ metadata (JSONB)    │       │
│ created_at          │       │
└──────────┬──────────┘       │
           │                  │
           │                  │
           ▼                  │
┌─────────────────────┐       │
│  recommendations    │       │
│─────────────────────│       │
│ id (PK)             │       │
│ waste_id (FK)       │───────┘
│ recommendation_type │
│ action_required     │
│ estimated_savings   │
│ implementation_     │
│   effort            │
│ status              │
│ created_at          │
│ applied_at          │
└──────────┬──────────┘
           │
           │
           ▼
┌─────────────────────┐
│  savings_realized   │
│─────────────────────│
│ id (PK)             │
│ recommendation_id   │
│   (FK)              │
│ applied_date        │
│ actual_monthly_     │
│   savings_eur       │
│ verification_method │
│ notes               │
│ created_at          │
└─────────────────────┘
```

### Table Details

#### `cloud_costs_raw`
**Purpose**: Store raw cost data from cloud providers

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| provider | VARCHAR(20) | aws, gcp, azure |
| account_id | VARCHAR(100) | Cloud account identifier |
| service | VARCHAR(100) | Service name (EC2, S3, RDS, etc.) |
| resource_id | VARCHAR(200) | Specific resource ID (nullable for aggregated data) |
| usage_date | DATE | Date of usage |
| cost | DECIMAL(12,4) | Cost amount |
| currency | VARCHAR(3) | USD, EUR, etc. |
| region | VARCHAR(50) | Cloud region |
| raw_data | JSONB | Original API response |
| created_at | TIMESTAMP | Insertion timestamp |

**Indexes**:
- `idx_costs_date` on `usage_date`
- `idx_costs_provider` on `provider`
- `idx_costs_service` on `service`

**Typical row**:
```json
{
  "id": 1234,
  "provider": "aws",
  "account_id": "123456789012",
  "service": "Amazon Elastic Compute Cloud",
  "resource_id": null,
  "usage_date": "2025-01-15",
  "cost": 245.67,
  "currency": "USD",
  "region": "eu-west-1",
  "raw_data": null,
  "created_at": "2025-01-16 08:00:00"
}
```

#### `ec2_metrics`
**Purpose**: Store CloudWatch metrics for EC2 instances

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| instance_id | VARCHAR(50) | EC2 instance ID |
| instance_type | VARCHAR(50) | Instance type (t3.micro, etc.) |
| instance_state | VARCHAR(20) | running, stopped, etc. |
| metric_date | DATE | Metric collection date |
| avg_cpu_percent | DECIMAL(5,2) | Average CPU over 24h |
| max_cpu_percent | DECIMAL(5,2) | Max CPU over 24h |
| avg_network_in_mb | DECIMAL(10,2) | Avg network in (MB) |
| tags | JSONB | Instance tags |
| created_at | TIMESTAMP | Insertion timestamp |

**Indexes**:
- `idx_ec2_metrics_instance` on `instance_id`
- `idx_ec2_metrics_date` on `metric_date`

**Typical row**:
```json
{
  "instance_id": "i-0abcd1234efgh5678",
  "instance_type": "t3.medium",
  "instance_state": "running",
  "metric_date": "2025-01-15",
  "avg_cpu_percent": 2.34,
  "max_cpu_percent": 8.91,
  "avg_network_in_mb": 12.45,
  "tags": {"Name": "web-server-prod", "Environment": "production"}
}
```

#### `waste_detected`
**Purpose**: Store identified waste opportunities

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| detection_date | DATE | When waste was detected |
| provider | VARCHAR(20) | Cloud provider |
| account_id | VARCHAR(100) | Cloud account |
| resource_id | VARCHAR(200) | Resource identifier |
| resource_type | VARCHAR(50) | ec2_instance, rds_instance, ebs_volume |
| waste_type | VARCHAR(50) | idle_compute, orphan_storage, oversized |
| monthly_waste_eur | DECIMAL(12,4) | Estimated monthly waste |
| confidence_score | DECIMAL(3,2) | 0.00-1.00 confidence |
| metadata | JSONB | Additional detection details |
| created_at | TIMESTAMP | Detection timestamp |

**Indexes**:
- `idx_waste_date` on `detection_date`
- `idx_waste_type` on `waste_type`

**Typical row**:
```json
{
  "detection_date": "2025-01-16",
  "provider": "aws",
  "account_id": "123456789012",
  "resource_id": "i-0abcd1234efgh5678",
  "resource_type": "ec2_instance",
  "waste_type": "idle_compute",
  "monthly_waste_eur": 156.80,
  "confidence_score": 0.92,
  "metadata": {
    "cpu_avg_7d": 2.34,
    "cpu_max_7d": 8.91,
    "instance_type": "t3.medium",
    "detection_method": "cloudwatch_cpu_avg"
  }
}
```

#### `recommendations`
**Purpose**: Actionable steps to eliminate waste

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| waste_id | INTEGER | FK to waste_detected |
| recommendation_type | VARCHAR(50) | stop_instance, resize, delete, schedule |
| action_required | TEXT | Human-readable action |
| estimated_monthly_savings_eur | DECIMAL(12,4) | Expected savings |
| implementation_effort | VARCHAR(20) | low, medium, high |
| status | VARCHAR(20) | pending, applied, rejected |
| created_at | TIMESTAMP | Recommendation creation |
| applied_at | TIMESTAMP | When action was taken |

**Indexes**:
- `idx_recommendations_status` on `status`
- `idx_recommendations_waste` on `waste_id`

**Typical row**:
```json
{
  "waste_id": 42,
  "recommendation_type": "stop_instance",
  "action_required": "Stop EC2 instance i-0abcd1234efgh5678 (avg CPU: 2.3%)",
  "estimated_monthly_savings_eur": 156.80,
  "implementation_effort": "low",
  "status": "pending"
}
```

#### `savings_realized`
**Purpose**: Track actual savings after applying recommendations

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| recommendation_id | INTEGER | FK to recommendations |
| applied_date | DATE | When action was applied |
| actual_monthly_savings_eur | DECIMAL(12,4) | Measured savings |
| verification_method | VARCHAR(50) | How savings were verified |
| notes | TEXT | Additional notes |
| created_at | TIMESTAMP | Record creation |

---

## 🏗️ Component Architecture

### Collectors

**Purpose**: Fetch data from cloud provider APIs

**Location**: `src/collectors/`

**Structure**:
```python
class AWSCostCollector:
    def __init__(self):
        # Initialize boto3 client
        
    def get_costs_last_n_days(self, days=30):
        # Call Cost Explorer API
        # Return DataFrame
        
    def save_to_postgres(self, df):
        # Batch insert to cloud_costs_raw
```

**Design Principles**:
- ✅ One collector = One data source
- ✅ Idempotent (can run multiple times safely)
- ✅ Fail gracefully (log errors, don't crash)
- ✅ Batch inserts (performance)
- ✅ ON CONFLICT DO NOTHING (avoid duplicates)

### Detectors

**Purpose**: Analyze data to find waste

**Location**: `src/detectors/`

**Structure**:
```python
class EC2IdleDetector:
    def detect(self):
        # Query ec2_metrics
        # Apply rules
        # Return waste list
        
    def calculate_waste(self, instance):
        # Get pricing
        # Calculate monthly cost
        # Return waste amount
```

**Detection Rules**:

| Rule | Condition | Confidence |
|------|-----------|------------|
| EC2 Idle | avg CPU < 5% (7d) | 0.90-0.99 |
| RDS Idle | connections = 0 (7d) | 0.85-0.95 |
| EBS Orphan | state = available | 1.00 |
| K8s Over | requests > usage×2 | 0.70-0.85 |

### Core Utilities

**Location**: `src/core/`

**Modules**:
- `database.py` - PostgreSQL connection management
- `config.py` - Environment variable handling
- `logger.py` - Structured logging
- `pricing.py` - Cloud pricing data

---

## 🔧 Technology Decisions

### Why PostgreSQL?

**Chosen**: PostgreSQL 16

**Alternatives considered**: MySQL, MongoDB, ClickHouse

**Decision rationale**:
- ✅ JSONB for flexible metadata
- ✅ Excellent performance for analytics
- ✅ Strong ecosystem (Metabase support)
- ✅ ACID compliance
- ✅ Free and open source
- ❌ ClickHouse overkill for <1M rows
- ❌ MongoDB lacks relational joins

### Why Metabase?

**Chosen**: Metabase OSS

**Alternatives considered**: Grafana, Superset, Tableau

**Decision rationale**:
- ✅ SQL-first (perfect for FinOps)
- ✅ CFO-friendly (business metrics)
- ✅ Open source
- ✅ Self-hosted
- ✅ Easy setup (Docker)
- ❌ Grafana too DevOps-focused
- ❌ Tableau not open source

### Why Python?

**Chosen**: Python 3.11+

**Alternatives considered**: Go, Node.js, Bash

**Decision rationale**:
- ✅ boto3 (AWS SDK) mature
- ✅ pandas for data manipulation
- ✅ Ecosystem for ML (future forecasting)
- ✅ Fast development
- ✅ Easy to read/maintain
- ❌ Go faster but longer dev time
- ❌ Bash too limited for complex logic

### Why Docker Compose?

**Chosen**: Docker Compose

**Alternatives considered**: Kubernetes, bare metal

**Decision rationale**:
- ✅ Simple for local dev
- ✅ Reproducible environments
- ✅ Easy for users to install
- ✅ No k8s complexity
- ❌ k8s overkill for MVP
- ❌ Bare metal hard to reproduce

---

## 🔐 Security Architecture

### Principle: Defense in Depth

**Layer 1: AWS IAM (Read-Only)**
```json
{
  "Effect": "Allow",
  "Action": [
    "ce:GetCostAndUsage",  // Read costs
    "cloudwatch:Get*",     // Read metrics
    "ec2:Describe*"        // Read resources
  ],
  "Resource": "*"
}
```
- ❌ No write permissions
- ❌ No instance termination
- ❌ No configuration changes

**Layer 2: Environment Variables**
- All secrets in `.env`
- Never committed to Git
- `.gitignore` enforced

**Layer 3: Docker Isolation**
- PostgreSQL not exposed publicly (localhost only)
- Metabase authentication required
- No root in containers

**Layer 4: PostgreSQL Security**
- Strong passwords
- User/database isolation
- Connection limits

**Phase 3+ (Production)**:
- VPC private subnets
- IAM Roles (no credentials)
- Secrets Manager
- TLS encryption
- WAF protection

---

## 📈 Scalability Considerations

### Current Limits (MVP)

| Resource | Limit | Notes |
|----------|-------|-------|
| AWS Accounts | 1 | Single account support |
| Cloud Providers | 1 (AWS) | GCP/Azure in Phase 2 |
| Data Retention | 90 days | Cost data |
| PostgreSQL Size | <10 GB | Typical for 1 account |
| Concurrent Users | 5-10 | Metabase limitation |

### Phase 2 Scaling (10 accounts)

**Approach**: Vertical scaling
- Larger PostgreSQL (RDS t3.medium)
- More RAM for collectors
- Partitioned tables by account

**Estimated limits**:
- 10 AWS accounts
- 100 GB PostgreSQL
- 50 concurrent Metabase users

### Phase 3+ Scaling (100+ accounts)

**Approach**: Horizontal scaling
- Kubernetes cluster
- PostgreSQL → Aurora
- Separate workers per account
- Redis for caching
- S3 for raw data archival

**Architecture shift**:
```
Load Balancer
    ↓
API Gateway (FastAPI)
    ↓
Worker Pool (Celery)
    ↓
Message Queue (RabbitMQ)
    ↓
PostgreSQL Cluster (Aurora)
```

---

## 🔄 Evolution Roadmap

### Phase 1 (Current)
```
Local Docker
├── PostgreSQL (1 instance)
├── Metabase (1 instance)
└── Python scripts (manual)
```

### Phase 2 (Months 4-6)
```
Local/VPS
├── PostgreSQL (scheduled backups)
├── Metabase (persistent config)
├── Cron jobs (automated collection)
└── Multi-account support
```

### Phase 3 (Months 7-12)
```
VPS/Cloud
├── PostgreSQL (managed RDS)
├── Metabase (HA setup)
├── API (FastAPI)
├── Workers (scheduled)
└── Multi-cloud (AWS + GCP)
```

### Phase 4+ (Scale)
```
Kubernetes
├── PostgreSQL (Aurora multi-AZ)
├── API Gateway (load balanced)
├── Worker Pool (auto-scaling)
├── Redis Cache
└── S3 Archive
```

---

## 🧪 Testing Strategy

### Unit Tests
**Target**: Core logic (detectors, calculators)
```python
def test_calculate_waste():
    detector = EC2IdleDetector()
    waste = detector.calculate_waste(
        instance_type='t3.medium',
        monthly_cost=100.0,
        cpu_avg=2.5
    )
    assert waste == 95.0  # 95% of cost
```

### Integration Tests
**Target**: Database interactions
```python
def test_save_to_postgres():
    collector = AWSCostCollector()
    df = pd.DataFrame([...])
    collector.save_to_postgres(df)
    # Verify data in PostgreSQL
```

### End-to-End Tests
**Target**: Complete workflows
```bash
# Collect → Detect → Verify
python src/aws_collector.py
python src/waste_detector.py
# Check PostgreSQL for results
```

---

## 📊 Monitoring & Observability

### Phase 1 (MVP)
- ✅ Console logs
- ✅ Docker logs (`docker-compose logs`)
- ✅ Manual SQL queries

### Phase 2+
- Structured logging (JSON)
- CloudWatch Logs
- Metabase usage tracking
- Error alerting (Slack)

### Phase 3+
- Prometheus metrics
- Grafana dashboards
- APM (Application Performance Monitoring)
- Distributed tracing

---

## 🔗 External Dependencies

### Required Services

| Service | Purpose | Cost | Criticality |
|---------|---------|------|-------------|
| AWS Cost Explorer | Cost data | Free* | Critical |
| AWS CloudWatch | Metrics | ~$10/mo | Critical |
| PostgreSQL | Storage | $0 (Docker) | Critical |
| Metabase | Visualization | $0 (OSS) | High |

*Free tier covers typical usage

### Optional Services (Phase 3+)

| Service | Purpose | Cost |
|---------|---------|------|
| AWS RDS | Managed PostgreSQL | $50+/mo |
| CloudWatch Logs | Centralized logging | ~$5/mo |
| Route 53 | DNS | $1/mo |
| ACM | SSL certificates | Free |

---

## 📝 Change Log

### v1.0 (December 2024)
- Initial architecture for MVP
- AWS-only support
- EC2 idle detection
- Local Docker deployment

### Planned

**v1.1** (Q1 2025)
- RDS idle detection
- EBS orphan detection
- Multi-account support

**v2.0** (Q2 2025)
- GCP support
- Kubernetes integration
- API layer

---

## 🎯 Architecture Principles

### 1. Simplicity First
Start simple, add complexity when needed.

### 2. Fail Gracefully
No single point of failure crashes entire system.

### 3. Observable
Every component logs its state.

### 4. Testable
Separate concerns, dependency injection.

### 5. Scalable
Design for 10x growth, not 100x (avoid premature optimization).

### 6. Secure by Default
Read-only access, no credentials in code.

---

## 📚 References

- [AWS Cost Explorer API](https://docs.aws.amazon.com/cost-management/latest/APIReference/)
- [PostgreSQL JSONB](https://www.postgresql.org/docs/current/datatype-json.html)
- [Metabase Documentation](https://www.metabase.com/docs/latest/)
- [boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)

---

**Document Owner**: [Your Name]  
**Last Review**: December 2024  
**Next Review**: March 2025