# Development Guide for Wasteless

> **Complete guide for local development, testing, and contributing to wasteless**

Version: 1.0  
Last Updated: December 2024  
Target Audience: Developers, Contributors

---

## 📋 Table of Contents

- [Prerequisites](#-prerequisites)
- [Local Development Setup](#-local-development-setup)
- [Project Structure](#-project-structure)
- [Development Workflow](#-development-workflow)
- [Adding New Features](#-adding-new-features)
- [Testing](#-testing)
- [Code Style & Standards](#-code-style--standards)
- [Debugging](#-debugging)
- [Database Management](#-database-management)
- [Common Tasks](#-common-tasks)
- [Performance](#-performance)
- [Troubleshooting](#-troubleshooting)

---

## 🛠️ Prerequisites

### Required Software

| Tool | Version | Installation |
|------|---------|--------------|
| Python | 3.11+ | `brew install python@3.11` (Mac) |
| Docker | 24.0+ | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| Docker Compose | 2.0+ | Included with Docker Desktop |
| Git | 2.30+ | `brew install git` (Mac) |
| AWS CLI | 2.x | `brew install awscli` (Mac) |

### Optional but Recommended

| Tool | Purpose | Installation |
|------|---------|--------------|
| pyenv | Python version management | `brew install pyenv` |
| direnv | Auto-load .env | `brew install direnv` |
| PostgreSQL Client | Database CLI | `brew install postgresql` |
| jq | JSON parsing | `brew install jq` |

### Development Tools (Python)

```bash
pip install --upgrade pip
pip install black ruff pytest pytest-cov ipython
```

---

## 🚀 Local Development Setup

### 1. Clone Repository

```bash
# Fork on GitHub first (if contributing)
git clone https://github.com/YOUR_USERNAME/wasteless.git
cd wasteless

# Add upstream remote
git remote add upstream https://github.com/wasteless-io/wasteless.git
```

### 2. Python Environment

```bash
# Create virtual environment
python3.11 -m venv venv

# Activate (do this every time you work on the project)
source venv/bin/activate  # Mac/Linux
# or
venv\Scripts\activate  # Windows

# Verify Python version
python --version  # Should be 3.11+

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Dev tools
```

### 3. Environment Configuration

```bash
# Copy template
cp .env.template .env

# Edit with your values
nano .env  # or vim, code, etc.
```

**Minimal .env for development**:

```bash
# AWS (use test/sandbox account if possible)
AWS_REGION=eu-west-1
AWS_ACCOUNT_ID=123456789012
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Database (default for Docker)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=finops
DB_USER=finops
DB_PASSWORD=finops_dev_2025

# Development
DEBUG=true
LOG_LEVEL=DEBUG
```

### 4. Start Docker Services

```bash
# Start PostgreSQL + Metabase
docker-compose up -d

# Verify containers are running
docker-compose ps

# Check logs if issues
docker-compose logs postgres
docker-compose logs metabase

# Wait for PostgreSQL to be ready (~30 seconds)
docker-compose exec postgres pg_isready -U finops
```

### 5. Verify Setup

```bash
# Test database connection
psql -h localhost -U finops -d finops -c "SELECT version();"

# Test Python environment
python -c "import boto3, pandas, psycopg2; print('✅ All imports OK')"

# Test AWS connection
python -c "
from dotenv import load_dotenv
import boto3
load_dotenv()
print(boto3.client('sts').get_caller_identity())
"
```

### 6. Initialize Database (First Time)

```bash
# Database is auto-initialized via init.sql
# Verify tables exist
psql -h localhost -U finops -d finops -c "\dt"

# Should show:
# cloud_costs_raw
# ec2_metrics  
# waste_detected
# recommendations
# savings_realized
```

### 7. First Data Collection

```bash
# Collect AWS costs
python src/collectors/aws_costs.py

# Collect EC2 metrics
python src/collectors/aws_cloudwatch.py

# Run waste detection
python src/detectors/ec2_idle.py

# Verify data
psql -h localhost -U finops -d finops -c "
SELECT COUNT(*) as total_costs FROM cloud_costs_raw;
SELECT COUNT(*) as total_waste FROM waste_detected;
"
```

---

## 📁 Project Structure

```
wasteless/
├── .github/                    # GitHub specific files
│   └── workflows/             # CI/CD (future)
│       └── tests.yml
├── src/                       # Main source code
│   ├── collectors/            # Data collection from cloud APIs
│   │   ├── __init__.py
│   │   ├── base.py           # Abstract base collector
│   │   ├── aws_costs.py      # AWS Cost Explorer
│   │   ├── aws_cloudwatch.py # CloudWatch metrics
│   │   └── aws_pricing.py    # Pricing data (future)
│   ├── detectors/             # Waste detection rules
│   │   ├── __init__.py
│   │   ├── base.py           # Abstract base detector
│   │   ├── ec2_idle.py       # EC2 idle instances
│   │   ├── rds_idle.py       # RDS idle (Phase 2)
│   │   └── ebs_orphan.py     # EBS orphaned volumes (Phase 2)
│   ├── core/                  # Shared utilities
│   │   ├── __init__.py
│   │   ├── database.py       # PostgreSQL connection
│   │   ├── config.py         # Configuration management
│   │   ├── logger.py         # Logging setup
│   │   └── exceptions.py     # Custom exceptions
│   └── utils/                 # Helper functions
│       ├── __init__.py
│       ├── pricing.py        # Cost calculations
│       ├── formatters.py     # Data formatting
│       └── aws_helpers.py    # AWS utility functions
├── sql/                       # Database scripts
│   ├── init.sql              # Initial schema
│   ├── migrations/           # Schema changes
│   │   ├── 001_add_ec2_metrics.sql
│   │   └── 002_add_rds_table.sql
│   └── queries/              # Useful queries
│       └── common_reports.sql
├── dashboards/               # Metabase exports
│   ├── metabase/
│   │   ├── aws_cost_overview.json
│   │   └── waste_detection.json
│   └── screenshots/
├── docs/                     # Documentation
│   ├── ARCHITECTURE.md
│   ├── AWS_SETUP.md
│   ├── DEVELOPMENT.md       # This file
│   └── DEPLOYMENT.md
├── tests/                    # Unit & integration tests
│   ├── __init__.py
│   ├── conftest.py          # Pytest fixtures
│   ├── test_collectors.py
│   ├── test_detectors.py
│   ├── test_database.py
│   └── fixtures/            # Test data
│       └── sample_aws_costs.json
├── scripts/                  # Automation scripts
│   ├── setup.sh             # Initial setup
│   ├── run_collectors.sh    # Run all collectors
│   ├── backup_db.sh         # Database backup
│   └── reset_db.sh          # Reset database
├── config/                   # Configuration files
│   ├── .env.template
│   └── logging.yaml
├── .gitignore
├── .dockerignore
├── docker-compose.yml
├── Dockerfile               # Future: containerized app
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
├── pyproject.toml          # Python project config
├── pytest.ini              # Pytest configuration
├── README.md
├── CHANGELOG.md
├── LICENSE
└── CONTRIBUTING.md
```

### File Naming Conventions

```
snake_case.py          # Python modules
ClassName              # Classes
function_name()        # Functions
CONSTANT_NAME          # Constants
_private_function()    # Private (internal use)
```

---

## 🔄 Development Workflow

### Daily Workflow

```bash
# 1. Start work session
cd wasteless
source venv/bin/activate
docker-compose up -d

# 2. Pull latest changes
git checkout develop
git pull upstream develop

# 3. Create feature branch
git checkout -b feature/your-feature-name

# 4. Make changes
# ... code code code ...

# 5. Test locally
python -m pytest
black src/
ruff check src/

# 6. Commit changes
git add .
git commit -m "feat: add RDS idle detector"

# 7. Push to your fork
git push origin feature/your-feature-name

# 8. Open Pull Request on GitHub

# 9. End work session
docker-compose down
deactivate
```

### Branch Strategy

```
main                  # Production-ready code
  └── develop         # Integration branch
      ├── feature/*   # New features
      ├── bugfix/*    # Bug fixes
      ├── docs/*      # Documentation
      └── refactor/*  # Code refactoring
```

**Branch naming**:
```bash
feature/rds-idle-detector
bugfix/postgres-connection-leak
docs/update-api-reference
refactor/extract-pricing-module
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
feat: add RDS idle instance detector
fix: PostgreSQL connection timeout after 1 hour
docs: update AWS setup guide with IAM examples
style: format code with black
refactor: extract cost calculation to utils
test: add unit tests for EC2 detector
chore: update boto3 to 1.35.0
```

**Format**:
```
<type>: <description>

[optional body]

[optional footer]
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting (no code change)
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance (deps, config)
- `perf`: Performance improvement

---

## ➕ Adding New Features

### Example: Adding a New Detector

Let's add an **RDS Idle Detector** step-by-step.

#### 1. Create Detector File

```bash
touch src/detectors/rds_idle.py
```

#### 2. Implement Detector

```python
# src/detectors/rds_idle.py
"""
RDS Idle Instance Detector

Detects RDS instances with zero database connections over 7 days.
"""

from src.detectors.base import BaseDetector
from src.core.database import get_db_connection
from src.core.logger import get_logger
import psycopg2

logger = get_logger(__name__)


class RDSIdleDetector(BaseDetector):
    """Detect idle RDS instances based on connection metrics."""
    
    def __init__(self):
        """Initialize RDS idle detector."""
        super().__init__()
        self.waste_type = 'idle_rds'
        self.resource_type = 'rds_instance'
        
    def detect(self, days=7, connection_threshold=0):
        """
        Detect RDS instances with low connection activity.
        
        Args:
            days (int): Number of days to analyze (default: 7)
            connection_threshold (int): Max avg connections to be considered idle (default: 0)
            
        Returns:
            list[dict]: List of idle RDS instances with waste details
            
        Example:
            >>> detector = RDSIdleDetector()
            >>> idle_instances = detector.detect(days=7)
            >>> print(f"Found {len(idle_instances)} idle RDS instances")
        """
        logger.info(f"Starting RDS idle detection (last {days} days)")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query RDS metrics
        query = """
        SELECT 
            instance_id,
            instance_type,
            AVG(avg_db_connections) as connections_avg_7d,
            MAX(max_db_connections) as connections_max_7d
        FROM rds_metrics
        WHERE metric_date >= CURRENT_DATE - INTERVAL '%s days'
        GROUP BY instance_id, instance_type
        HAVING AVG(avg_db_connections) <= %s;
        """
        
        cursor.execute(query, (days, connection_threshold))
        idle_instances = cursor.fetchall()
        
        logger.info(f"Found {len(idle_instances)} idle RDS instances")
        
        # Calculate waste for each instance
        waste_list = []
        for instance in idle_instances:
            instance_id, instance_type, conn_avg, conn_max = instance
            
            # Get monthly cost (from pricing API or database)
            monthly_cost = self._get_instance_monthly_cost(instance_type)
            
            # Calculate waste (100% if truly idle)
            monthly_waste = monthly_cost * 1.0  # Full cost is waste
            
            # Confidence score
            confidence = 1.0 if conn_max == 0 else 0.95
            
            waste_list.append({
                'resource_id': instance_id,
                'resource_type': self.resource_type,
                'waste_type': self.waste_type,
                'monthly_waste_eur': monthly_waste,
                'confidence_score': confidence,
                'metadata': {
                    'connections_avg_7d': float(conn_avg),
                    'connections_max_7d': float(conn_max),
                    'instance_type': instance_type,
                    'detection_method': 'cloudwatch_db_connections'
                }
            })
        
        cursor.close()
        conn.close()
        
        return waste_list
    
    def _get_instance_monthly_cost(self, instance_type):
        """
        Get monthly cost for RDS instance type.
        
        Args:
            instance_type (str): RDS instance type (e.g., 'db.t3.micro')
            
        Returns:
            float: Monthly cost in EUR
        """
        # TODO: Implement pricing API lookup
        # For now, use static pricing
        pricing = {
            'db.t3.micro': 15.0,
            'db.t3.small': 30.0,
            'db.t3.medium': 60.0,
            'db.m5.large': 140.0,
        }
        
        return pricing.get(instance_type, 50.0)  # Default if not found


def main():
    """CLI entry point for testing."""
    detector = RDSIdleDetector()
    idle_instances = detector.detect(days=7)
    
    print(f"\n{'='*60}")
    print(f"RDS Idle Detection Results")
    print(f"{'='*60}")
    print(f"Idle instances found: {len(idle_instances)}")
    
    total_waste = sum(i['monthly_waste_eur'] for i in idle_instances)
    print(f"Total monthly waste: €{total_waste:,.2f}")
    
    # Save to database
    if idle_instances:
        # Implement save logic
        print(f"\n✅ Saved {len(idle_instances)} waste records to database")
    

if __name__ == '__main__':
    main()
```

#### 3. Add Base Detector Class (if not exists)

```python
# src/detectors/base.py
"""Base detector class for all waste detectors."""

from abc import ABC, abstractmethod


class BaseDetector(ABC):
    """Abstract base class for waste detectors."""
    
    def __init__(self):
        """Initialize base detector."""
        self.waste_type = None
        self.resource_type = None
    
    @abstractmethod
    def detect(self, **kwargs):
        """
        Detect waste resources.
        
        Returns:
            list[dict]: List of waste records
        """
        pass
```

#### 4. Add Database Migration

```sql
-- sql/migrations/002_add_rds_metrics.sql

CREATE TABLE IF NOT EXISTS rds_metrics (
    id SERIAL PRIMARY KEY,
    instance_id VARCHAR(100) NOT NULL,
    instance_type VARCHAR(50),
    instance_state VARCHAR(20),
    metric_date DATE NOT NULL,
    avg_db_connections DECIMAL(10, 2),
    max_db_connections DECIMAL(10, 2),
    avg_cpu_percent DECIMAL(5, 2),
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_rds_metrics_instance ON rds_metrics(instance_id);
CREATE INDEX idx_rds_metrics_date ON rds_metrics(metric_date);

COMMENT ON TABLE rds_metrics IS 'CloudWatch metrics for RDS instances';
```

Apply migration:
```bash
psql -h localhost -U finops -d finops -f sql/migrations/002_add_rds_metrics.sql
```

#### 5. Add Tests

```python
# tests/test_detectors.py
import pytest
from src.detectors.rds_idle import RDSIdleDetector


def test_rds_idle_detector_init():
    """Test RDS idle detector initialization."""
    detector = RDSIdleDetector()
    
    assert detector.waste_type == 'idle_rds'
    assert detector.resource_type == 'rds_instance'


def test_rds_idle_detector_pricing():
    """Test RDS pricing lookup."""
    detector = RDSIdleDetector()
    
    cost = detector._get_instance_monthly_cost('db.t3.micro')
    assert cost == 15.0
    
    cost_unknown = detector._get_instance_monthly_cost('db.unknown.type')
    assert cost_unknown == 50.0  # Default


@pytest.mark.integration
def test_rds_idle_detector_detect(db_with_sample_data):
    """Test RDS idle detection with sample data."""
    detector = RDSIdleDetector()
    
    # This requires test database with sample RDS metrics
    idle_instances = detector.detect(days=7)
    
    assert isinstance(idle_instances, list)
    assert all('resource_id' in i for i in idle_instances)
    assert all('monthly_waste_eur' in i for i in idle_instances)
```

#### 6. Update Documentation

```markdown
# Update README.md

## Features

- ✅ EC2 idle instance detection
- ✅ RDS idle instance detection (NEW!)
- ⏳ EBS orphaned volumes
```

```markdown
# Update CHANGELOG.md

## [Unreleased]

### Added
- RDS idle instance detector
- RDS metrics table
```

#### 7. Test Locally

```bash
# Run unit tests
pytest tests/test_detectors.py -v

# Run detector manually
python src/detectors/rds_idle.py

# Verify in database
psql -h localhost -U finops -d finops -c "
SELECT * FROM waste_detected WHERE waste_type = 'idle_rds';
"
```

#### 8. Commit and Push

```bash
git add src/detectors/rds_idle.py
git add src/detectors/base.py
git add sql/migrations/002_add_rds_metrics.sql
git add tests/test_detectors.py
git add README.md CHANGELOG.md

git commit -m "feat: add RDS idle instance detector

- Detect RDS instances with 0 connections over 7 days
- Add rds_metrics table
- Add unit tests
- Update documentation"

git push origin feature/rds-idle-detector
```

---

## 🧪 Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_collectors.py

# Run specific test
pytest tests/test_collectors.py::test_aws_cost_collector

# Run with verbose output
pytest -v

# Run and stop at first failure
pytest -x

# Run only tests marked as "unit"
pytest -m unit

# Skip slow integration tests
pytest -m "not integration"
```

### Writing Tests

#### Unit Test Example

```python
# tests/test_utils.py
import pytest
from src.utils.pricing import calculate_monthly_cost


def test_calculate_monthly_cost():
    """Test monthly cost calculation."""
    # Hourly cost * 730 hours/month
    hourly = 0.05
    monthly = calculate_monthly_cost(hourly)
    
    assert monthly == 36.5  # 0.05 * 730
    

def test_calculate_monthly_cost_zero():
    """Test monthly cost with zero input."""
    assert calculate_monthly_cost(0) == 0


def test_calculate_monthly_cost_negative():
    """Test monthly cost with negative input."""
    with pytest.raises(ValueError):
        calculate_monthly_cost(-10)
```

#### Integration Test Example

```python
# tests/test_database.py
import pytest
from src.core.database import get_db_connection


@pytest.mark.integration
def test_database_connection():
    """Test PostgreSQL connection."""
    conn = get_db_connection()
    
    assert conn is not None
    
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    
    assert result[0] == 1
    
    cursor.close()
    conn.close()


@pytest.mark.integration
def test_insert_cost_data(db_connection):
    """Test inserting cost data."""
    cursor = db_connection.cursor()
    
    cursor.execute("""
        INSERT INTO cloud_costs_raw (provider, service, usage_date, cost)
        VALUES ('aws', 'EC2', '2025-01-01', 100.50)
        RETURNING id;
    """)
    
    result = cursor.fetchone()
    assert result is not None
    
    db_connection.rollback()  # Don't persist test data
```

#### Fixtures

```python
# tests/conftest.py
import pytest
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()


@pytest.fixture(scope="session")
def db_connection():
    """Provide database connection for tests."""
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )
    
    yield conn
    
    conn.close()


@pytest.fixture
def sample_costs():
    """Provide sample cost data for tests."""
    return [
        {'service': 'EC2', 'cost': 100.0, 'date': '2025-01-01'},
        {'service': 'RDS', 'cost': 50.0, 'date': '2025-01-01'},
        {'service': 'S3', 'cost': 10.0, 'date': '2025-01-01'},
    ]
```

### Test Markers

```python
# Mark tests by type
@pytest.mark.unit
def test_something():
    pass

@pytest.mark.integration
def test_database():
    pass

@pytest.mark.slow
def test_full_collection():
    pass
```

Configure in `pytest.ini`:
```ini
[pytest]
markers =
    unit: Unit tests (fast, no external dependencies)
    integration: Integration tests (database, AWS)
    slow: Slow tests (full data collection)
```

---

## 🎨 Code Style & Standards

### Python Style Guide

We follow **PEP 8** with some modifications:

```python
# Line length: 100 characters (not 79)
# Use Black for formatting (handles this automatically)

# Imports
import os
import sys
from datetime import datetime

import boto3
import pandas as pd

from src.core.database import get_db_connection
from src.utils.pricing import calculate_cost

# Constants
MAX_RETRIES = 3
DEFAULT_REGION = 'eu-west-1'

# Functions
def calculate_waste(instance_type: str, cpu_avg: float) -> float:
    """
    Calculate monthly waste for an instance.
    
    Args:
        instance_type: EC2 instance type
        cpu_avg: Average CPU utilization (0-100)
        
    Returns:
        Monthly waste in EUR
    """
    # Implementation
    pass

# Classes
class WasteDetector:
    """Detect cloud resource waste."""
    
    def __init__(self, provider: str = 'aws'):
        """Initialize detector."""
        self.provider = provider
    
    def detect(self) -> list[dict]:
        """Detect waste resources."""
        pass
```

### Formatting with Black

```bash
# Format all code
black src/ tests/

# Check without modifying
black --check src/

# Format specific file
black src/collectors/aws_costs.py
```

### Linting with Ruff

```bash
# Lint all code
ruff check src/

# Auto-fix issues
ruff check src/ --fix

# Lint specific file
ruff check src/collectors/aws_costs.py
```

### Type Hints

Use type hints for function signatures:

```python
from typing import Optional, List, Dict

def get_costs(
    start_date: str,
    end_date: str,
    services: Optional[List[str]] = None
) -> Dict[str, float]:
    """Get costs for date range."""
    pass
```

### Docstrings

Use **Google style** docstrings:

```python
def detect_idle_instances(cpu_threshold: float, days: int = 7) -> List[dict]:
    """
    Detect idle EC2 instances based on CPU utilization.
    
    Analyzes CloudWatch CPU metrics over the specified period and identifies
    instances with average CPU below the threshold.
    
    Args:
        cpu_threshold: CPU percentage threshold (0-100). Instances below
            this are considered idle.
        days: Number of days to analyze. Defaults to 7.
        
    Returns:
        List of dictionaries containing idle instance details:
            - instance_id (str): EC2 instance ID
            - monthly_waste_eur (float): Estimated monthly waste
            - confidence_score (float): Detection confidence (0-1)
            
    Raises:
        DatabaseError: If unable to query metrics table.
        ValueError: If cpu_threshold is not between 0 and 100.
        
    Example:
        >>> detector = EC2IdleDetector()
        >>> idle = detector.detect_idle_instances(cpu_threshold=5.0)
        >>> print(f"Found {len(idle)} idle instances")
        Found 3 idle instances
    """
    pass
```

---

## 🐛 Debugging

### Logging

```python
# src/collectors/aws_costs.py
from src.core.logger import get_logger

logger = get_logger(__name__)

def collect_costs():
    logger.debug("Starting cost collection")
    logger.info(f"Collecting costs for {days} days")
    logger.warning("Cost data incomplete for 2025-01-15")
    logger.error("Failed to connect to Cost Explorer API")
    logger.critical("Database connection lost")
```

Configure logging in `config/logging.yaml`:
```yaml
version: 1
formatters:
  default:
    format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
handlers:
  console:
    class: logging.StreamHandler
    formatter: default
    level: DEBUG
  file:
    class: logging.FileHandler
    filename: logs/wasteless.log
    formatter: default
    level: INFO
root:
  level: DEBUG
  handlers: [console, file]
```

### Interactive Debugging

```python
# Add breakpoint in code
import pdb; pdb.set_trace()

# Or use built-in breakpoint() (Python 3.7+)
breakpoint()
```

**PDB commands**:
```
n      # Next line
s      # Step into function
c      # Continue execution
l      # List source code
p var  # Print variable
q      # Quit debugger
```

### IPython for Exploration

```bash
# Start IPython shell with environment loaded
ipython

# Import and test
from src.collectors.aws_costs import AWSCostCollector
collector = AWSCostCollector()
df = collector.get_costs_last_n_days(days=7)
df.head()
```

### Database Debugging

```bash
# Connect to PostgreSQL
psql -h localhost -U finops -d finops

# Useful queries
SELECT COUNT(*) FROM cloud_costs_raw;
SELECT * FROM waste_detected ORDER BY monthly_waste_eur DESC LIMIT 5;
SELECT DISTINCT waste_type FROM waste_detected;

# Show table schema
\d cloud_costs_raw

# Exit
\q
```

---

## 🗄️ Database Management

### Common Operations

```bash
# Access PostgreSQL CLI
psql -h localhost -U finops -d finops

# Backup database
pg_dump -h localhost -U finops finops > backup_$(date +%Y%m%d).sql

# Restore database
psql -h localhost -U finops -d finops < backup_20250115.sql

# Reset database (WARNING: Deletes all data)
docker-compose down -v
docker-compose up -d
# Wait 30 seconds for init.sql to run
```

### Migrations

```bash
# Create new migration
cat > sql/migrations/003_add_k8s_metrics.sql << 'EOF'
CREATE TABLE k8s_metrics (
    id SERIAL PRIMARY KEY,
    cluster_name VARCHAR(100),
    namespace VARCHAR(100),
    pod_name VARCHAR(200),
    metric_date DATE NOT NULL,
    cpu_requests DECIMAL(10, 4),
    cpu_usage DECIMAL(10, 4),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_k8s_metrics_cluster ON k8s_metrics(cluster_name);
EOF

# Apply migration
psql -h localhost -U finops -d finops -f sql/migrations/003_add_k8s_metrics.sql

# Verify
psql -h localhost -U finops -d finops -c "\dt"
```

### Useful SQL Queries

```sql
-- Total waste by type
SELECT 
    waste_type,
    COUNT(*) as count,
    SUM(monthly_waste_eur) as total_waste
FROM waste_detected
WHERE detection_date >= CURRENT_DATE - 30
GROUP BY waste_type
ORDER BY total_waste DESC;

-- Top 10 most expensive idle instances
SELECT 
    resource_id,
    monthly_waste_eur,
    confidence_score,
    metadata->>'instance_type' as instance_type
FROM waste_detected
WHERE waste_type = 'idle_compute'
ORDER BY monthly_waste_eur DESC
LIMIT 10;

-- Cost trend over time
SELECT 
    usage_date,
    SUM(cost) as daily_cost
FROM cloud_costs_raw
WHERE usage_date >= CURRENT_DATE - 30
GROUP BY usage_date
ORDER BY usage_date;
```

---

## 📝 Common Tasks

### Task: Add a New AWS Collector

1. Create file in `src/collectors/`
2. Extend `BaseCollector`
3. Implement `collect()` method
4. Add database schema if needed
5. Add tests
6. Update documentation

### Task: Add a New Waste Detection Rule

1. Create file in `src/detectors/`
2. Extend `BaseDetector`
3. Implement `detect()` method
4. Add calculation logic
5. Add tests
6. Update documentation

### Task: Update Dependencies

```bash
# Check outdated packages
pip list --outdated

# Update specific package
pip install --upgrade boto3

# Update requirements file
pip freeze > requirements.txt

# Test that everything still works
pytest
```

### Task: Generate Sample Data

```python
# scripts/generate_sample_data.py
import random
from datetime import datetime, timedelta
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
cursor = conn.cursor()

# Generate 30 days of sample cost data
services = ['EC2', 'RDS', 'S3', 'Lambda', 'CloudWatch']
for i in range(30):
    date = (datetime.now().date() - timedelta(days=i))
    for service in services:
        cost = random.uniform(10, 500)
        cursor.execute("""
            INSERT INTO cloud_costs_raw (provider, service, usage_date, cost, currency)
            VALUES ('aws', %s, %s, %s, 'USD')
        """, (service, date, cost))

conn.commit()
print("✅ Sample data generated")
```

---

## ⚡ Performance

### Optimization Tips

1. **Batch Database Operations**
   ```python
   # ❌ Slow - One insert per row
   for row in data:
       cursor.execute("INSERT INTO ...", row)
   
   # ✅ Fast - Batch insert
   from psycopg2.extras import execute_values
   execute_values(cursor, "INSERT INTO ... VALUES %s", data)
   ```

2. **Use Database Indexes**
   ```sql
   CREATE INDEX idx_costs_date ON cloud_costs_raw(usage_date);
   ```

3. **Limit API Calls**
   ```python
   # Cache pricing data
   # Don't call AWS Pricing API for each instance
   ```

4. **Use Pandas Efficiently**
   ```python
   # ❌ Slow - Looping through DataFrame
   for _, row in df.iterrows():
       process(row)
   
   # ✅ Fast - Vectorized operations
   df['waste'] = df['cost'] * 0.95
   ```

### Profiling

```python
# Profile code execution time
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

# Code to profile
collector = AWSCostCollector()
collector.run()

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(10)  # Top 10 slowest functions
```

---

## 🔧 Troubleshooting

### Common Issues

#### Issue: Docker containers won't start

```bash
# Check if ports are in use
lsof -i :5432
lsof -i :3000

# Kill processes using ports
kill -9 <PID>

# Remove old containers
docker-compose down -v
docker-compose up -d
```

#### Issue: Python module not found

```bash
# Verify virtual environment is activated
which python  # Should point to venv/bin/python

# Reinstall dependencies
pip install -r requirements.txt
```

#### Issue: Database connection fails

```bash
# Check container is running
docker-compose ps

# Check logs
docker-compose logs postgres

# Verify credentials in .env
cat .env | grep DB_

# Test connection
psql -h localhost -U finops -d finops -c "SELECT 1"
```

---

## 📚 Additional Resources

### Internal Documentation
- [Architecture](ARCHITECTURE.md)
- [AWS Setup](AWS_SETUP.md)
- [Contributing](../CONTRIBUTING.md)
- [Deployment](DEPLOYMENT.md)

### External Resources
- [Python Style Guide](https://peps.python.org/pep-0008/)
- [boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [pytest Documentation](https://docs.pytest.org/)

---

## ✅ Development Checklist

Before submitting PR:
- [ ] Code formatted with Black
- [ ] No linting errors (Ruff)
- [ ] All tests pass (`pytest`)
- [ ] New features have tests
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] Commit messages follow convention
- [ ] `.env` not committed
- [ ] No hardcoded credentials
- [ ] Code reviewed locally

---

**Happy coding! 🚀**

Questions? Open an issue or ask in discussions.