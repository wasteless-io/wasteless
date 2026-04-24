# AWS Setup Guide for Wasteless

> **Complete guide to configure AWS access for wasteless platform**

Version: 1.0  
Last Updated: December 2025  
Estimated Time: 30-45 minutes

---

## 📋 Overview

Wasteless needs **read-only access** to your AWS account to:
- ✅ Collect cost data (Cost Explorer API)
- ✅ Fetch resource metrics (CloudWatch API)
- ✅ List EC2/RDS/EBS resources (Describe APIs)

**Important**: Wasteless **NEVER** modifies your infrastructure. All permissions are read-only.

---

## 🎯 Prerequisites

Before starting, ensure you have:
- [ ] AWS Account (existing or new)
- [ ] Admin access to AWS Console
- [ ] AWS CLI installed (optional but recommended)
- [ ] 30 minutes

---

## 🚀 Quick Setup (TL;DR)

```bash
# 1. Create IAM user "wasteless-readonly"
# 2. Attach policies: ViewOnlyAccess + Custom Cost Explorer policy
# 3. Download credentials
# 4. Enable Cost Explorer (if not already enabled)
# 5. Add credentials to .env file
```

**Full instructions below** ⬇️

---

## 📝 Step-by-Step Setup

### Step 1: Access IAM Console

1. Log in to [AWS Console](https://console.aws.amazon.com/)
2. Search for **"IAM"** in the top search bar
3. Click on **IAM** (Identity and Access Management)

**Alternative via CLI**:
```bash
aws iam get-user
# Should display your current user
```

---

### Step 2: Create IAM User

#### 2.1 Create User

1. In IAM Console, click **Users** (left sidebar)
2. Click **Add users** (orange button)
3. Enter user details:
   - **User name**: `wasteless-readonly`
   - **Access type**: ✅ **Access key - Programmatic access**
   - ❌ **Do NOT check** "Password - AWS Management Console access"
4. Click **Next: Permissions**

**Why programmatic access only?**
- No console login needed (security)
- Only API access via credentials
- Credentials can be rotated easily

#### 2.2 Set Permissions

**Option A: Using Managed Policies (Recommended for MVP)**

1. Click **Attach existing policies directly**
2. Search and select:
   - ✅ **ViewOnlyAccess** (AWS managed policy)
3. Click **Next: Tags**

**Option B: Using Custom Policy Group (More Secure)**

Skip to [Step 3: Create Custom IAM Policy](#step-3-create-custom-iam-policy) first, then return here.

#### 2.3 Add Tags (Optional but Recommended)

Add tags for organization:

| Key | Value |
|-----|-------|
| `Application` | `wasteless` |
| `Purpose` | `cost-analysis` |
| `Environment` | `production` |

Click **Next: Review**

#### 2.4 Review and Create

1. Review user details:
   - User name: `wasteless-readonly`
   - AWS access type: Programmatic access
   - Permissions: ViewOnlyAccess (+ custom policy if created)
2. Click **Create user**

#### 2.5 Download Credentials

⚠️ **CRITICAL STEP - Do this NOW**

1. You'll see a success screen with credentials
2. Click **Download .csv** button
3. Save the file securely (e.g., `wasteless-aws-credentials.csv`)
4. **This is your ONLY chance to download the secret access key**

The CSV contains:
```
User name,Password,Access key ID,Secret access key,Console login link
wasteless-readonly,,AKIA...,wJalr...,...
```

5. Click **Close**

**Security Best Practices**:
- ✅ Store CSV in password manager (1Password, LastPass, etc.)
- ✅ Delete CSV from Downloads folder after storing
- ❌ Never commit to Git
- ❌ Never share via email/Slack

---

### Step 3: Create Custom IAM Policy

For **maximum security**, create a minimal custom policy instead of using `ViewOnlyAccess`.

#### 3.1 Create Policy

1. In IAM Console, click **Policies** (left sidebar)
2. Click **Create policy**
3. Click **JSON** tab
4. Replace content with this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WastelessCostExplorerReadOnly",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "ce:GetCostAndUsageWithResources",
        "ce:GetReservationUtilization",
        "ce:GetSavingsPlansUtilization"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WastelessCloudWatchReadOnly",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WastelessEC2ReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeVolumes",
        "ec2:DescribeSnapshots",
        "ec2:DescribeImages",
        "ec2:DescribeRegions",
        "ec2:DescribeAvailabilityZones"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WastelessRDSReadOnly",
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "rds:DescribeDBClusters",
        "rds:DescribeDBSnapshots",
        "rds:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WastelessS3ReadOnly",
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "s3:GetBucketTagging",
        "s3:GetBucketVersioning"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WastelessEKSReadOnly",
      "Effect": "Allow",
      "Action": [
        "eks:ListClusters",
        "eks:DescribeCluster",
        "eks:ListNodegroups",
        "eks:DescribeNodegroup"
      ],
      "Resource": "*"
    }
  ]
}
```

#### 3.2 Review Policy

1. Click **Next: Tags** (add same tags as user if desired)
2. Click **Next: Review**
3. Enter policy details:
   - **Name**: `WastelessReadOnlyAccess`
   - **Description**: `Read-only access for wasteless cost optimization platform`
4. Click **Create policy**

#### 3.3 Attach Policy to User

1. Go back to **IAM → Users**
2. Click on **wasteless-readonly**
3. Click **Add permissions** → **Attach existing policies directly**
4. Search for `WastelessReadOnlyAccess`
5. Check the policy
6. Click **Add permissions**

---

### Step 4: Enable AWS Cost Explorer

Cost Explorer must be enabled to access cost data via API.

#### 4.1 Check if Already Enabled

```bash
# Using AWS CLI
aws ce get-cost-and-usage \
  --time-period Start=2025-01-01,End=2025-01-02 \
  --granularity DAILY \
  --metrics UnblendedCost

# If you get data → already enabled ✅
# If error "not subscribed" → continue below
```

#### 4.2 Enable Cost Explorer

1. Go to [AWS Cost Explorer](https://console.aws.amazon.com/cost-management/home#/cost-explorer)
2. Click **Enable Cost Explorer** (if you see this button)
3. Wait for confirmation (instant)

**Important**:
- ✅ Cost Explorer is **free** to enable
- ⚠️ Historical data takes **24 hours** to populate
- ℹ️ You can still use wasteless immediately (limited data initially)

#### 4.3 Verify Access

```bash
# Test with wasteless IAM user credentials
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="eu-west-1"

aws ce get-cost-and-usage \
  --time-period Start=$(date -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --metrics UnblendedCost

# Should return cost data (even if empty)
```

---

### Step 5: Get Your AWS Account ID

You'll need your 12-digit Account ID for wasteless configuration.

#### Method 1: AWS Console

1. Click your **username** (top-right corner)
2. Account ID is displayed in the dropdown

Example: `123456789012`

#### Method 2: AWS CLI

```bash
aws sts get-caller-identity --query Account --output text
```

#### Method 3: IAM Console

1. Go to IAM Dashboard
2. Look for **Account ID** at the top

---

### Step 6: Configure Wasteless

#### 6.1 Add Credentials to .env

In your wasteless project directory:

```bash
cd wasteless
cp .env.template .env
nano .env  # or vim, code, etc.
```

Edit `.env`:

```bash
# ============================================
# AWS Configuration
# ============================================

# Your AWS region (where most resources are)
AWS_REGION=eu-west-1

# Your 12-digit AWS Account ID
AWS_ACCOUNT_ID=123456789012

# Credentials from CSV file
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=wJalr...

# ============================================
# Database Configuration (default for local)
# ============================================
DB_HOST=localhost
DB_PORT=5432
DB_NAME=finops
DB_USER=finops
DB_PASSWORD=finops_dev_2025

# ============================================
# Metabase
# ============================================
METABASE_URL=http://localhost:3000
```

**Save and close**

#### 6.2 Verify Configuration

```bash
# Activate virtual environment
source venv/bin/activate

# Test AWS connection
python -c "
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

client = boto3.client('sts',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION')
)

identity = client.get_caller_identity()
print(f'✅ Connected as: {identity[\"Arn\"]}')
print(f'✅ Account ID: {identity[\"Account\"]}')
"
```

Expected output:
```
✅ Connected as: arn:aws:iam::123456789012:user/wasteless-readonly
✅ Account ID: 123456789012
```

---

## 🔐 Security Best Practices

### ✅ Do's

1. **Use minimal permissions**
   - Start with custom policy (Step 3)
   - Add permissions only when needed

2. **Rotate credentials regularly**
   - Every 90 days minimum
   - Use AWS Secrets Manager for production

3. **Enable MFA on your admin account**
   - Not on the wasteless user (it's programmatic only)
   - Protects your console access

4. **Monitor usage**
   - Check CloudTrail logs
   - Review IAM Access Advisor

5. **Use environment variables**
   - Never hardcode credentials
   - `.env` in `.gitignore`

### ❌ Don'ts

1. **Never commit credentials to Git**
   - Always use `.env` (gitignored)
   - Use secrets management in production

2. **Don't grant write permissions**
   - Wasteless only needs read access
   - Review policies regularly

3. **Don't share credentials**
   - Each environment = separate credentials
   - Revoke if compromised

4. **Don't use root account credentials**
   - Always create IAM users
   - Root = full account access (dangerous)

---

## 🔍 Troubleshooting

### Issue 1: "Access Denied" on Cost Explorer

**Symptoms**:
```
botocore.exceptions.ClientError: An error occurred (AccessDeniedException) 
when calling the GetCostAndUsage operation: User is not authorized
```

**Solutions**:

1. **Check Cost Explorer is enabled**
   ```bash
   # Try in AWS Console first
   # Go to Cost Explorer → Should see data
   ```

2. **Verify IAM policy includes Cost Explorer**
   ```bash
   aws iam list-attached-user-policies --user-name wasteless-readonly
   
   # Should show WastelessReadOnlyAccess or ViewOnlyAccess
   ```

3. **Check inline policies**
   ```bash
   aws iam list-user-policies --user-name wasteless-readonly
   ```

4. **Wait 24 hours after enabling Cost Explorer**
   - Data needs time to populate

---

### Issue 2: "Not subscribed to AWS Cost Explorer"

**Symptoms**:
```
OptInRequiredException: You are not subscribed to AWS Cost Explorer
```

**Solution**:
1. Go to [Cost Explorer Console](https://console.aws.amazon.com/cost-management/home#/cost-explorer)
2. Click "Enable Cost Explorer"
3. Wait 24 hours for data

---

### Issue 3: Invalid Credentials

**Symptoms**:
```
botocore.exceptions.ClientError: An error occurred (InvalidClientTokenId)
```

**Solutions**:

1. **Check credentials are correct**
   ```bash
   # From the CSV file
   AWS_ACCESS_KEY_ID=AKIA...  # Should start with AKIA
   AWS_SECRET_ACCESS_KEY=...  # Long string (40 chars)
   ```

2. **Test with AWS CLI**
   ```bash
   aws sts get-caller-identity
   # Should show user ARN
   ```

3. **Regenerate credentials if needed**
   - IAM → Users → wasteless-readonly
   - Security credentials tab
   - Create access key
   - Delete old key

---

### Issue 4: No Data Returned

**Symptoms**:
```python
# Cost Explorer returns empty results
{
  "ResultsByTime": []
}
```

**Solutions**:

1. **Check date range**
   ```python
   # Make sure dates are in the past
   start_date = "2025-01-01"
   end_date = "2025-01-15"  # Not in the future
   ```

2. **Verify you have AWS spend**
   - Cost Explorer Console → Check if you see costs
   - New accounts might have $0 spend

3. **Wait 24 hours after account creation**
   - Cost data needs time to aggregate

---

### Issue 5: Region Mismatch

**Symptoms**:
```
# Metrics not found or instances not listed
```

**Solutions**:

1. **Cost Explorer is global** (any region works)
2. **CloudWatch/EC2 are regional**:
   ```bash
   # Check which region your resources are in
   aws ec2 describe-instances --region eu-west-1
   aws ec2 describe-instances --region us-east-1
   ```

3. **Update .env with correct region**:
   ```bash
   AWS_REGION=eu-west-1  # Or your region
   ```

---

## 🧪 Testing Your Setup

### Test 1: AWS Authentication

```bash
python -c "
import boto3
from dotenv import load_dotenv
load_dotenv()

sts = boto3.client('sts')
print(sts.get_caller_identity())
"

# Expected: {'UserId': '...', 'Account': '123456789012', 'Arn': '...'}
```

### Test 2: Cost Explorer Access

```bash
python -c "
import boto3
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

ce = boto3.client('ce')
end = datetime.now().date()
start = end - timedelta(days=7)

response = ce.get_cost_and_usage(
    TimePeriod={'Start': str(start), 'End': str(end)},
    Granularity='DAILY',
    Metrics=['UnblendedCost']
)

print(f'✅ Cost Explorer access: OK')
print(f'Data points: {len(response[\"ResultsByTime\"])}')
"
```

### Test 3: CloudWatch Access

```bash
python -c "
import boto3
from dotenv import load_dotenv
load_dotenv()

cw = boto3.client('cloudwatch')
metrics = cw.list_metrics(Namespace='AWS/EC2', MaxRecords=1)

print(f'✅ CloudWatch access: OK')
"
```

### Test 4: EC2 Describe Access

```bash
python -c "
import boto3
from dotenv import load_dotenv
load_dotenv()

ec2 = boto3.client('ec2')
instances = ec2.describe_instances(MaxResults=5)

print(f'✅ EC2 Describe access: OK')
print(f'Reservations found: {len(instances[\"Reservations\"])}')
"
```

### Test 5: Run Wasteless Collector

```bash
# Full integration test
python src/aws_collector.py

# Should output:
# ✅ Connexion AWS OK
# 📊 Collecte des coûts...
# ✅ X lignes collectées
# 💾 Y lignes insérées dans PostgreSQL
```

---

## 📊 Understanding AWS Costs for Wasteless

### Free Tier

Wasteless uses **only free tier services** in most cases:

| Service | Cost | Notes |
|---------|------|-------|
| Cost Explorer API | **Free** | Standard calls included |
| CloudWatch API | **Free** | 1M API requests/month free |
| EC2 Describe APIs | **Free** | No charge for describe operations |
| IAM | **Free** | Always free |

### Potential Costs

| Service | Cost | When |
|---------|------|------|
| CloudWatch Detailed Monitoring | ~$3/instance/month | If enabled (NOT required) |
| CloudWatch Logs | ~$0.50/GB | If storing logs (optional) |
| Data Transfer | Negligible | API responses are small |

**Total expected cost**: **$0-5/month** (typically $0)

---

## 🔄 Credential Rotation

### When to Rotate

- ✅ Every 90 days (good practice)
- ✅ If credentials potentially compromised
- ✅ When team member leaves
- ✅ Regular security audits

### How to Rotate

1. **Create new credentials**:
   - IAM → Users → wasteless-readonly
   - Security credentials tab
   - **Create access key**
   - Download new credentials

2. **Update .env**:
   ```bash
   # Replace with new credentials
   AWS_ACCESS_KEY_ID=AKIA...NEW...
   AWS_SECRET_ACCESS_KEY=...NEW...
   ```

3. **Test new credentials**:
   ```bash
   python src/aws_collector.py
   ```

4. **Delete old credentials**:
   - IAM → Users → wasteless-readonly
   - Security credentials tab
   - **Deactivate** old key
   - Wait 24h to confirm nothing breaks
   - **Delete** old key

---

## 🌍 Multi-Account Setup (Phase 2+)

For organizations with multiple AWS accounts:

### Option A: Cross-Account Role (Recommended)

1. **Create role in target account**:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {
         "AWS": "arn:aws:iam::MAIN_ACCOUNT:user/wasteless-readonly"
       },
       "Action": "sts:AssumeRole"
     }]
   }
   ```

2. **Attach read-only policies to role**

3. **Assume role from wasteless**:
   ```python
   sts = boto3.client('sts')
   assumed = sts.assume_role(
       RoleArn='arn:aws:iam::TARGET_ACCOUNT:role/WastelessRole',
       RoleSessionName='wasteless-session'
   )
   ```

### Option B: Separate IAM Users

Create `wasteless-readonly` user in each account (simpler but less secure).

---

## 📚 Additional Resources

### AWS Documentation

- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Cost Explorer API Reference](https://docs.aws.amazon.com/cost-management/latest/APIReference/)
- [CloudWatch API Reference](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/)
- [IAM Policy Examples](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_examples.html)

### Wasteless Documentation

- [Architecture](ARCHITECTURE.md) - Technical architecture
- [README](../README.md) - Project overview
- [Development](DEVELOPMENT.md) - Development guide

---

## ✅ Setup Checklist

Use this checklist to verify your setup:

- [ ] IAM user `wasteless-readonly` created
- [ ] Credentials downloaded and saved securely
- [ ] Custom IAM policy created and attached
- [ ] Cost Explorer enabled
- [ ] AWS Account ID obtained
- [ ] `.env` file configured
- [ ] AWS CLI test successful (`aws sts get-caller-identity`)
- [ ] Cost Explorer API test successful
- [ ] CloudWatch API test successful
- [ ] Wasteless collector test successful (`python src/aws_collector.py`)
- [ ] Credentials added to password manager
- [ ] CSV file deleted from Downloads
- [ ] `.env` confirmed in `.gitignore`

---

## 🆘 Getting Help

If you're stuck:

1. **Check troubleshooting section** above
2. **Review AWS CloudTrail logs** for denied API calls
3. **Open GitHub issue** with error details (anonymize account ID!)
4. **Email**: support@wasteless.io
5. **Slack**: [Join our community]

---

## 🔒 Security Contacts

**Found a security issue?**

🔴 **Do NOT open a public GitHub issue**

📧 Email: security@wasteless.io  
🔐 PGP Key: [Available on request]

---

**Document Version**: 1.0  
**Last Updated**: December 2024  
**Next Review**: March 2025

---

✅ **Setup complete!** You can now run wasteless to start detecting cloud waste.

Next steps:
1. [Run the first collection](../README.md#usage)
2. [View dashboards in Metabase](../README.md#access-dashboards)
3. [Explore detected waste](../README.md#detect-waste)