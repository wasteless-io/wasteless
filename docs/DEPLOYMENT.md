# Deployment Guide for Wasteless

> **Complete guide for deploying wasteless in production environments**

Version: 1.0  
Last Updated: December 2025  
Target Audience: DevOps, SRE, Solo Founders

---

## 📋 Table of Contents

- [Deployment Options](#-deployment-options)
- [VPS Deployment (Recommended for MVP)](#-vps-deployment-recommended-for-mvp)
- [Client AWS Account Deployment](#-client-aws-account-deployment)
- [Production Checklist](#-production-checklist)
- [Security Hardening](#-security-hardening)
- [Monitoring & Alerting](#-monitoring--alerting)
- [Backup & Disaster Recovery](#-backup--disaster-recovery)
- [Scaling](#-scaling)
- [Troubleshooting](#-troubleshooting)

---

## 🎯 Deployment Options

### Decision Matrix

| Option | When to Use | Cost | Complexity | Control |
|--------|-------------|------|------------|---------|
| **Local** | Development only | $0 | Low | Full |
| **VPS** | Demos, 1-5 clients | €10-30/mo | Low | Full |
| **Client AWS** | 5-50 clients | €0 (client pays) | Medium | High |
| **SaaS Multi-tenant** | 50+ clients | €500+/mo | High | Full |

### Recommended Path

```
Phase 1-2 (MVP)
    → Local (laptop)

Phase 3 (Demos)
    → VPS (Hetzner/OVH)

Phase 4 (First Clients)
    → Deploy in Client AWS Accounts (Terraform)

Phase 5 (Scale)
    → SaaS Multi-tenant (Kubernetes)
```

---

## 🖥️ VPS Deployment (Recommended for MVP)

**Use case**: Demos, POC, 1-5 pilot clients

**Cost**: €10-30/month

**Time**: 1-2 hours

### 1. Choose VPS Provider

**Recommended Providers**:

| Provider | Config | Price | Bandwidth | Notes |
|----------|--------|-------|-----------|-------|
| **Hetzner** | CX21 (2vCPU, 4GB) | €5.83/mo | 20TB | Best value |
| **DigitalOcean** | Basic (2vCPU, 4GB) | $24/mo | 4TB | Easy to use |
| **OVH** | VPS Starter | €6/mo | Unlimited | EU-based |
| **Linode** | Nanode 4GB | $24/mo | 4TB | Good support |

**Recommendation**: **Hetzner CX21** for best price/performance.

### 2. Provision VPS

#### Via Hetzner Cloud Console

1. Go to [Hetzner Cloud](https://console.hetzner.cloud/)
2. Create project: `wasteless-production`
3. Add server:
   - **Location**: Nuremberg (eu-central) or Helsinki
   - **Image**: Ubuntu 24.04 LTS
   - **Type**: CX21 (2 vCPU, 4 GB RAM, 40 GB SSD)
   - **SSH Key**: Add your public key
   - **Name**: `wasteless-vps-01`
4. Click **Create & Buy**
5. Note the IPv4 address

#### Via CLI (Alternative)

```bash
# Install Hetzner CLI
brew install hcloud  # Mac
# or
wget https://github.com/hetznercloud/cli/releases/download/v1.42.0/hcloud-linux-amd64.tar.gz

# Login
hcloud context create wasteless

# Create server
hcloud server create \
  --type cx21 \
  --image ubuntu-24.04 \
  --name wasteless-vps-01 \
  --ssh-key YOUR_SSH_KEY_NAME \
  --location nbg1

# Get IP
hcloud server ip wasteless-vps-01
```

### 3. Initial Server Setup

```bash
# SSH into server
ssh root@YOUR_VPS_IP

# Update system
apt update && apt upgrade -y

# Install essential packages
apt install -y \
  curl \
  git \
  vim \
  htop \
  ufw \
  fail2ban \
  unattended-upgrades

# Create wasteless user
useradd -m -s /bin/bash wasteless
usermod -aG sudo wasteless

# Setup SSH key for wasteless user
mkdir -p /home/wasteless/.ssh
cp /root/.ssh/authorized_keys /home/wasteless/.ssh/
chown -R wasteless:wasteless /home/wasteless/.ssh
chmod 700 /home/wasteless/.ssh
chmod 600 /home/wasteless/.ssh/authorized_keys

# Test SSH as wasteless
# exit
# ssh wasteless@YOUR_VPS_IP
```

### 4. Configure Firewall

```bash
# Enable UFW
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (IMPORTANT: Do this first!)
ufw allow 22/tcp

# Allow HTTP/HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Allow PostgreSQL (only from localhost)
# Don't expose 5432 to internet!

# Enable firewall
ufw enable

# Check status
ufw status verbose
```

### 5. Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Add wasteless user to docker group
usermod -aG docker wasteless

# Install Docker Compose
apt install -y docker-compose-plugin

# Verify
docker --version
docker compose version

# Enable Docker to start on boot
systemctl enable docker
```

### 6. Setup Wasteless

```bash
# Switch to wasteless user
su - wasteless

# Clone repository
git clone https://github.com/wasteless-io/wasteless.git
cd wasteless

# Create production .env
cp .env.template .env
nano .env
```

**Production .env**:
```bash
# AWS Configuration
AWS_REGION=eu-west-1
AWS_ACCOUNT_ID=123456789012
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Database
DB_HOST=postgres  # Docker container name
DB_PORT=5432
DB_NAME=finops
DB_USER=finops
DB_PASSWORD=STRONG_PASSWORD_HERE  # Change this!

# Metabase
METABASE_URL=https://wasteless.yourdomain.com

# Production settings
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
```

### 7. Setup Docker Compose for Production

```bash
# Create docker-compose.prod.yml
nano docker-compose.prod.yml
```

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    container_name: wasteless-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./sql/init.sql:/docker-entrypoint-initdb.d/init.sql
      - ./backups:/backups
    networks:
      - wasteless-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  metabase:
    image: metabase/metabase:v0.48.0
    container_name: wasteless-metabase
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      MB_DB_TYPE: postgres
      MB_DB_DBNAME: metabase
      MB_DB_PORT: 5432
      MB_DB_USER: ${DB_USER}
      MB_DB_PASS: ${DB_PASSWORD}
      MB_DB_HOST: postgres
    volumes:
      - metabase_data:/metabase-data
    networks:
      - wasteless-network
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.metabase.rule=Host(`wasteless.yourdomain.com`)"
      - "traefik.http.routers.metabase.entrypoints=websecure"
      - "traefik.http.routers.metabase.tls.certresolver=letsencrypt"

  traefik:
    image: traefik:v2.10
    container_name: wasteless-traefik
    restart: unless-stopped
    command:
      - "--api.insecure=false"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.tlschallenge=true"
      - "--certificatesresolvers.letsencrypt.acme.email=you@example.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
      - "./letsencrypt:/letsencrypt"
    networks:
      - wasteless-network

volumes:
  postgres_data:
  metabase_data:

networks:
  wasteless-network:
    driver: bridge
```

### 8. Configure Domain & DNS

1. **Buy domain** (if not already done):
   - Namecheap, CloudFlare, OVH, etc.
   - Example: `wasteless-demo.com`

2. **Configure DNS A record**:
   ```
   Type: A
   Name: @ (or wasteless)
   Value: YOUR_VPS_IP
   TTL: 300
   ```

3. **Wait for DNS propagation** (5-30 minutes):
   ```bash
   nslookup wasteless-demo.com
   # Should return your VPS IP
   ```

### 9. Start Services

```bash
# Start containers
docker compose -f docker-compose.prod.yml up -d

# Check status
docker compose -f docker-compose.prod.yml ps

# Check logs
docker compose -f docker-compose.prod.yml logs -f

# Wait for services to be healthy
# PostgreSQL: ~30 seconds
# Metabase: ~2 minutes (first start)
# Traefik: ~10 seconds
```

### 10. Setup Cron Jobs

```bash
# Edit crontab
crontab -e
```

Add these jobs:

```cron
# Collect AWS costs daily at 2 AM
0 2 * * * cd /home/wasteless/wasteless && /home/wasteless/wasteless/venv/bin/python src/collectors/aws_costs.py >> /home/wasteless/logs/collector.log 2>&1

# Collect CloudWatch metrics daily at 3 AM
0 3 * * * cd /home/wasteless/wasteless && /home/wasteless/wasteless/venv/bin/python src/collectors/aws_cloudwatch.py >> /home/wasteless/logs/cloudwatch.log 2>&1

# Run waste detection daily at 4 AM
0 4 * * * cd /home/wasteless/wasteless && /home/wasteless/wasteless/venv/bin/python src/detectors/ec2_idle.py >> /home/wasteless/logs/detector.log 2>&1

# Backup database daily at 1 AM
0 1 * * * /home/wasteless/wasteless/scripts/backup_db.sh >> /home/wasteless/logs/backup.log 2>&1

# Clean old logs weekly (Sunday at midnight)
0 0 * * 0 find /home/wasteless/logs -name "*.log" -mtime +30 -delete
```

Create log directory:
```bash
mkdir -p /home/wasteless/logs
```

### 11. Create Backup Script

```bash
# Create backup script
nano scripts/backup_db.sh
```

```bash
#!/bin/bash
# Database backup script

BACKUP_DIR="/home/wasteless/wasteless/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="wasteless_backup_${DATE}.sql"

# Create backup
docker exec wasteless-postgres pg_dump -U finops finops > "${BACKUP_DIR}/${BACKUP_FILE}"

# Compress
gzip "${BACKUP_DIR}/${BACKUP_FILE}"

# Keep only last 30 days
find ${BACKUP_DIR} -name "*.sql.gz" -mtime +30 -delete

echo "Backup completed: ${BACKUP_FILE}.gz"
```

Make executable:
```bash
chmod +x scripts/backup_db.sh
mkdir -p backups
```

### 12. Verify Deployment

```bash
# Check website is accessible
curl -I https://wasteless-demo.com
# Should return: HTTP/2 200

# Check SSL certificate
curl https://wasteless-demo.com
# Should work without SSL errors

# Check containers
docker ps
# All should be Up

# Check logs
docker compose -f docker-compose.prod.yml logs --tail=50

# Test database
docker exec wasteless-postgres psql -U finops -d finops -c "SELECT COUNT(*) FROM cloud_costs_raw;"
```

### 13. Access Metabase

1. Open browser: `https://wasteless-demo.com`
2. First time: Setup admin account
3. Connect to PostgreSQL database (see AWS_SETUP.md)
4. Import dashboards from `dashboards/metabase/`

---

## ☁️ Client AWS Account Deployment

**Use case**: Deploy wasteless in client's AWS account for maximum security and compliance.

**Benefits**:
- ✅ Client data stays in their account
- ✅ Client controls infrastructure
- ✅ Easier compliance (GDPR, SOC2)
- ✅ No hosting costs for you

**Complexity**: Medium (requires Terraform knowledge)

### Architecture

```
Client AWS Account
├── VPC (10.0.0.0/16)
│   ├── Public Subnet (10.0.1.0/24)
│   │   └── Application Load Balancer
│   └── Private Subnet (10.0.10.0/24)
│       ├── ECS Fargate (wasteless containers)
│       └── RDS PostgreSQL (waste data)
├── S3 Bucket (backups)
├── CloudWatch Logs (application logs)
└── IAM Role (read-only access to client resources)
```

### Prerequisites

- Terraform installed
- AWS CLI configured with client account credentials
- Client has enabled Cost Explorer

### 1. Create Terraform Configuration

Directory structure:
```
terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── vpc.tf
├── ecs.tf
├── rds.tf
└── iam.tf
```

**variables.tf**:
```hcl
variable "aws_region" {
  description = "AWS region"
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Project name"
  default     = "wasteless"
}

variable "environment" {
  description = "Environment (prod, staging)"
  default     = "prod"
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "client_name" {
  description = "Client name for tagging"
  type        = string
}
```

**vpc.tf**:
```hcl
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project_name}-vpc"
    Environment = var.environment
    Client      = var.client_name
  }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${var.project_name}-public-${count.index + 1}"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${var.project_name}-private-${count.index + 1}"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# NAT Gateway (for private subnets to access internet)
resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "${var.project_name}-nat"
  }
}

# Route tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

# Route table associations
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
```

**rds.tf**:
```hcl
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-db-subnet"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-rds-sg"
  }
}

resource "aws_db_instance" "main" {
  identifier           = "${var.project_name}-db"
  engine               = "postgres"
  engine_version       = "16.1"
  instance_class       = "db.t3.micro"
  allocated_storage    = 20
  storage_type         = "gp3"
  storage_encrypted    = true
  
  db_name  = "finops"
  username = "finops"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "sun:04:00-sun:05:00"

  skip_final_snapshot = false
  final_snapshot_identifier = "${var.project_name}-final-snapshot"

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = {
    Name        = "${var.project_name}-db"
    Environment = var.environment
  }
}
```

**iam.tf**:
```hcl
# IAM Role for ECS Task
resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

# Policy for reading cost and usage data
resource "aws_iam_role_policy" "wasteless_readonly" {
  name = "${var.project_name}-readonly-policy"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostForecast",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "rds:DescribeDBInstances"
        ]
        Resource = "*"
      }
    ]
  })
}
```

**ecs.tf**:
```hcl
resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "${var.project_name}-cluster"
  }
}

resource "aws_security_group" "ecs" {
  name        = "${var.project_name}-ecs-sg"
  description = "Security group for ECS tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-ecs-sg"
  }
}

# ECS Task Definition
resource "aws_ecs_task_definition" "metabase" {
  family                   = "${var.project_name}-metabase"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "metabase"
    image = "metabase/metabase:v0.48.0"
    
    portMappings = [{
      containerPort = 3000
      protocol      = "tcp"
    }]

    environment = [
      {
        name  = "MB_DB_TYPE"
        value = "postgres"
      },
      {
        name  = "MB_DB_DBNAME"
        value = aws_db_instance.main.db_name
      },
      {
        name  = "MB_DB_PORT"
        value = "5432"
      },
      {
        name  = "MB_DB_USER"
        value = aws_db_instance.main.username
      },
      {
        name  = "MB_DB_PASS"
        value = aws_db_instance.main.password
      },
      {
        name  = "MB_DB_HOST"
        value = aws_db_instance.main.address
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/${var.project_name}"
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "metabase"
      }
    }
  }])
}

# ECS Service
resource "aws_ecs_service" "metabase" {
  name            = "${var.project_name}-metabase"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.metabase.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.metabase.arn
    container_name   = "metabase"
    container_port   = 3000
  }

  depends_on = [aws_lb_listener.https]
}
```

### 2. Deploy with Terraform

```bash
# Initialize Terraform
cd terraform/
terraform init

# Create workspace for client
terraform workspace new client-acme

# Plan deployment
terraform plan \
  -var="client_name=acme-corp" \
  -var="db_password=SECURE_PASSWORD_HERE" \
  -out=tfplan

# Review plan carefully

# Apply
terraform apply tfplan

# Save outputs
terraform output > outputs.txt
```

### 3. Post-Deployment Configuration

```bash
# Get RDS endpoint
terraform output rds_endpoint

# Get ALB DNS name
terraform output alb_dns_name

# Configure DNS CNAME
# wasteless.acme-corp.com → ALB_DNS_NAME

# SSH into ECS task or use ECS Exec to run collectors
# (Configure cron via EventBridge Scheduler)
```

---

## ✅ Production Checklist

Before going live:

### Security
- [ ] HTTPS enabled with valid SSL certificate
- [ ] Firewall configured (only necessary ports open)
- [ ] SSH key-based authentication (no passwords)
- [ ] Fail2ban installed and configured
- [ ] Regular security updates enabled
- [ ] Strong database passwords
- [ ] AWS IAM using read-only policies
- [ ] Secrets not in Git (.env in .gitignore)

### Reliability
- [ ] Automated backups configured (daily)
- [ ] Backup restoration tested
- [ ] Health checks configured
- [ ] Container restart policies set
- [ ] Monitoring in place
- [ ] Alerting configured
- [ ] Log rotation enabled

### Performance
- [ ] Database indexed properly
- [ ] Cron jobs scheduled optimally
- [ ] No resource leaks
- [ ] Efficient SQL queries

### Documentation
- [ ] Deployment documented
- [ ] Runbook created
- [ ] Emergency contacts listed
- [ ] Credentials stored securely (password manager)

---

## 🔐 Security Hardening

### SSH Hardening

```bash
# Edit SSH config
sudo nano /etc/ssh/sshd_config
```

```
# Disable root login
PermitRootLogin no

# Disable password authentication
PasswordAuthentication no
PubkeyAuthentication yes

# Change default port (optional but recommended)
Port 2222

# Limit users who can SSH
AllowUsers wasteless
```

Restart SSH:
```bash
sudo systemctl restart sshd
```

### Fail2ban Configuration

```bash
# Install
sudo apt install fail2ban

# Configure
sudo nano /etc/fail2ban/jail.local
```

```ini
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = 2222
logpath = /var/log/auth.log
```

Start service:
```bash
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

### SSL/TLS with Let's Encrypt

Already configured in docker-compose.prod.yml via Traefik.

To manually renew:
```bash
docker compose -f docker-compose.prod.yml restart traefik
```

### Database Security

```sql
-- Connect as postgres superuser
-- Revoke public access
REVOKE ALL ON DATABASE finops FROM PUBLIC;

-- Create read-only user for Metabase if needed
CREATE USER metabase_readonly WITH PASSWORD 'secure_password';
GRANT CONNECT ON DATABASE finops TO metabase_readonly;
GRANT USAGE ON SCHEMA public TO metabase_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase_readonly;
```

---

## 📊 Monitoring & Alerting

### Docker Health Checks

Already configured in docker-compose files.

Check health:
```bash
docker inspect wasteless-postgres | grep -A 10 "Health"
```

### Basic Monitoring Script

```bash
# Create monitoring script
nano scripts/health_check.sh
```

```bash
#!/bin/bash

# Check if containers are running
if ! docker ps | grep -q "wasteless-postgres"; then
    echo "⚠️ PostgreSQL container is not running!"
    # Send alert (email, Slack, etc.)
fi

if ! docker ps | grep -q "wasteless-metabase"; then
    echo "⚠️ Metabase container is not running!"
fi

# Check disk space
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 80 ]; then
    echo "⚠️ Disk usage is ${DISK_USAGE}%!"
fi

# Check database connection
if ! docker exec wasteless-postgres pg_isready -U finops > /dev/null 2>&1; then
    echo "⚠️ PostgreSQL is not accepting connections!"
fi

echo "✅ All checks passed"
```

Add to cron:
```cron
*/15 * * * * /home/wasteless/wasteless/scripts/health_check.sh
```

### CloudWatch Monitoring (AWS Deployment)

Metrics automatically collected:
- ECS CPU/Memory utilization
- RDS connections, CPU, storage
- Application logs

Access via AWS Console → CloudWatch.

### Uptime Monitoring

Use external service:
- [UptimeRobot](https://uptimerobot.com/) (free)
- [Pingdom](https://www.pingdom.com/)
- [Better Uptime](https://betteruptime.com/)

Configure to ping `https://wasteless.yourdomain.com` every 5 minutes.

---

## 💾 Backup & Disaster Recovery

### Automated Backups

Already configured via cron (see VPS deployment section).

### Manual Backup

```bash
# Database
docker exec wasteless-postgres pg_dump -U finops finops > backup_$(date +%Y%m%d).sql

# Entire PostgreSQL data directory
docker run --rm -v wasteless_postgres_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/postgres_data_$(date +%Y%m%d).tar.gz -C /data .

# Metabase configuration
docker run --rm -v wasteless_metabase_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/metabase_data_$(date +%Y%m%d).tar.gz -C /data .
```

### Restore from Backup

```bash
# Stop containers
docker compose -f docker-compose.prod.yml down

# Restore database
cat backup_20250115.sql | docker exec -i wasteless-postgres psql -U finops -d finops

# Or restore data directory
docker run --rm -v wasteless_postgres_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/postgres_data_20250115.tar.gz -C /data

# Start containers
docker compose -f docker-compose.prod.yml up -d
```

### Off-site Backups

Upload to S3:
```bash
# Install AWS CLI
pip install awscli

# Configure
aws configure

# Upload backups
aws s3 sync backups/ s3://wasteless-backups-client-name/
```

Add to cron after backup:
```cron
30 1 * * * aws s3 sync /home/wasteless/wasteless/backups/ s3://wasteless-backups/
```

---

## 📈 Scaling

### Vertical Scaling (VPS)

When to upgrade:
- CPU usage consistently > 70%
- Memory usage > 80%
- Database slow queries

Upgrade path:
```
CX21 (2vCPU, 4GB)  → €5.83/mo
CX31 (2vCPU, 8GB)  → €10.83/mo
CX41 (4vCPU, 16GB) → €20.83/mo
```

### Horizontal Scaling (Multi-container)

Add more collectors running in parallel:

```yaml
# docker-compose.prod.yml
services:
  collector-1:
    # Collect accounts 1-50
  collector-2:
    # Collect accounts 51-100
```

### Database Scaling

1. **Add read replicas** (PostgreSQL replication)
2. **Partition tables** by date or account_id
3. **Upgrade to managed RDS** (AWS)

---

## 🔧 Troubleshooting

### Issue: Containers won't start

```bash
# Check logs
docker compose -f docker-compose.prod.yml logs

# Check disk space
df -h

# Check memory
free -h

# Restart containers
docker compose -f docker-compose.prod.yml restart
```

### Issue: Database connection errors

```bash
# Check PostgreSQL is running
docker ps | grep postgres

# Check if accepting connections
docker exec wasteless-postgres pg_isready -U finops

# Check logs
docker logs wasteless-postgres

# Test connection
docker exec -it wasteless-postgres psql -U finops -d finops
```

### Issue: SSL certificate not working

```bash
# Check Traefik logs
docker logs wasteless-traefik

# Verify domain DNS
nslookup wasteless.yourdomain.com

# Check certificate
curl -vI https://wasteless.yourdomain.com

# Force certificate renewal
docker exec wasteless-traefik rm /letsencrypt/acme.json
docker restart wasteless-traefik
```

### Issue: High memory usage

```bash
# Check container stats
docker stats

# If PostgreSQL is using too much memory
docker exec wasteless-postgres psql -U finops -d finops -c "
  SELECT pg_size_pretty(pg_database_size('finops'));
"

# Vacuum database
docker exec wasteless-postgres psql -U finops -d finops -c "VACUUM FULL;"
```

---

## 📚 Additional Resources

### Internal Documentation
- [Architecture](ARCHITECTURE.md)
- [AWS Setup](AWS_SETUP.md)
- [Development](DEVELOPMENT.md)
- [Contributing](../CONTRIBUTING.md)

### External Resources
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [PostgreSQL Backup](https://www.postgresql.org/docs/current/backup.html)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/)

---

## 🆘 Support

**Production issues?**

1. Check logs: `docker compose logs`
2. Check this troubleshooting section
3. Check GitHub issues
4. Email: support@wasteless.io
5. Slack: [Join community]

**Security issues?**

📧 security@wasteless.io (do not open public issue)

---

**Document Version**: 1.0  
**Last Updated**: December 2024  
**Next Review**: March 2025

---

✅ **Ready to deploy!**

Choose your path:
- **Demo/POC** → VPS Deployment
- **Client deployment** → Client AWS Account
- **Scale** → Multi-tenant SaaS (contact us)