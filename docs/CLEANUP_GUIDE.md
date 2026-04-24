# Cleanup Orphaned Recommendations - Guide

## 📌 Overview

This guide explains how to manage recommendations for AWS resources that have been manually deleted outside of Wasteless (e.g., terminated in AWS Console).

## 🎯 Problem

When you manually delete EC2 instances in the AWS Console, Wasteless doesn't automatically detect this change. The recommendations for these deleted instances remain in the database with status `pending` or `applied`, causing them to still appear in your dashboards.

## ✅ Solution

Wasteless provides an automated cleanup utility that:
1. Scans all EC2 instances currently in your AWS account
2. Compares with recommendations in the database
3. Marks recommendations as `obsolete` when the instance no longer exists
4. Removes them from your dashboards and reports

---

## 🚀 Quick Start

### Manual Cleanup (One-Time)

```bash
# 1. Check what would be cleaned (safe, no changes)
python src/utils/cleanup_orphaned_recommendations.py --dry-run

# 2. Execute cleanup (if satisfied with dry-run results)
python src/utils/cleanup_orphaned_recommendations.py

# 3. View logs
tail -f logs/cleanup_*.log
```

### Automated Cleanup (Recommended)

Install a cron job to run cleanup automatically:

```bash
# Install with interactive setup
./scripts/install_cron.sh install

# You'll be prompted to choose a schedule:
#   1) Daily at 3:00 AM (Recommended)
#   2) Every 12 hours (3:00 AM and 3:00 PM)
#   3) Every 6 hours
#   4) Custom schedule
```

---

## 📖 Detailed Usage

### Check Cron Job Status

```bash
./scripts/install_cron.sh status
```

Output example:
```
======================================================================
📊 Wasteless Cleanup Cron Job Status
======================================================================
✅ Cron job is INSTALLED

Current crontab entry:
---
# Wasteless: Automated cleanup of orphaned recommendations
0 3 * * * /path/to/wasteless/scripts/run_cleanup.sh
---

Recent logs:
-rw-r--r--  1 user  staff  2.1K Jan 11 03:00 cleanup_20260111_030000.log
-rw-r--r--  1 user  staff  1.9K Jan 10 03:00 cleanup_20260110_030000.log
```

### Remove Cron Job

```bash
./scripts/install_cron.sh remove
```

### Manual Script Execution

The wrapper script handles all setup automatically:

```bash
./scripts/run_cleanup.sh
```

This script:
- Activates the virtual environment
- Runs the cleanup utility
- Logs all output to timestamped log files
- Cleans up logs older than 30 days

---

## 📊 Understanding the Output

### Dry-Run Mode

```bash
$ python src/utils/cleanup_orphaned_recommendations.py --dry-run

======================================================================
📊 CLEANUP SUMMARY
======================================================================
Total active recommendations: 5
Valid recommendations: 0
Orphaned recommendations: 5
======================================================================

🗑️  ORPHANED RECOMMENDATIONS DETAILS:
----------------------------------------------------------------------
  • Instance: i-0be60e8a844c3c89b
    Recommendation ID: 18
    Status: pending
    Type: downsize_instance
    Detection Date: 2025-12-22
```

**Explanation:**
- **Total active recommendations:** All non-obsolete recommendations in DB
- **Valid recommendations:** Recommendations for instances that still exist in AWS
- **Orphaned recommendations:** Recommendations for deleted instances (will be marked as obsolete)

### Live Mode

```bash
$ python src/utils/cleanup_orphaned_recommendations.py

======================================================================
✅ Successfully marked 5 recommendations as obsolete
======================================================================
```

**What happens:**
- Recommendations status changed: `pending`/`applied` → `obsolete`
- They disappear from active dashboards
- Historical data preserved for auditing

---

## 🔍 Logs

### Log Location

```bash
logs/cleanup_YYYYMMDD_HHMMSS.log
```

### Log Retention

Logs are automatically cleaned up after 30 days by the wrapper script.

### View Recent Logs

```bash
# View latest log
ls -t logs/cleanup_*.log | head -1 | xargs cat

# Monitor in real-time
tail -f logs/cleanup_$(date +%Y%m%d)_*.log

# View all logs from today
cat logs/cleanup_$(date +%Y%m%d)_*.log
```

---

## 🛠️ Advanced Usage

### Custom Cron Schedule

Examples of cron schedules:

```bash
# Every day at 3:00 AM
0 3 * * *

# Every 12 hours (3 AM and 3 PM)
0 3,15 * * *

# Every 6 hours
0 */6 * * *

# Every Monday at 2 AM
0 2 * * 1

# First day of month at midnight
0 0 1 * *
```

### Manual Cron Installation

If you prefer to configure cron manually:

```bash
# Edit crontab
crontab -e

# Add this line (adjust path):
0 3 * * * /path/to/wasteless/scripts/run_cleanup.sh
```

### Integration with Other Tools

You can integrate the cleanup script with monitoring tools:

```bash
# Example: Send notification on failure
./scripts/run_cleanup.sh || curl -X POST https://hooks.slack.com/... -d '{"text":"Cleanup failed"}'

# Example: Prometheus metrics export
./scripts/run_cleanup.sh && echo "wasteless_cleanup_success 1" > /var/lib/node_exporter/wasteless.prom
```

---

## 🔧 Troubleshooting

### Issue: Cron job not running

**Check cron service:**
```bash
# macOS
sudo launchctl list | grep cron

# Linux
sudo systemctl status cron
```

**Verify crontab:**
```bash
crontab -l
```

**Check logs:**
```bash
# macOS
tail -f /var/log/cron.log

# Linux
tail -f /var/log/syslog | grep CRON
```

### Issue: Virtual environment not found

Ensure the virtual environment exists:
```bash
ls -la venv/bin/activate
```

If missing, recreate it:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Issue: Database connection error

Verify database is running:
```bash
docker ps | grep postgres
```

Check environment variables:
```bash
cat .env | grep DB_
```

### Issue: AWS credentials not found

The script uses the same AWS credentials as the main application:
```bash
# Check .env file
cat .env | grep AWS_

# Or use AWS CLI credentials
aws configure list
```

---

## 📚 Related Documentation

- [Main README](../README.md) - Installation and setup
- [Remediation Configuration](../config/remediation.yaml) - Safeguard settings
- [Database Schema](../README.md#-database-schema) - Table structure

---

## ❓ FAQ

### Q: Will this delete my instances in AWS?

**A:** No. This script only updates the database. It never touches AWS resources.

### Q: What happens to "obsolete" recommendations?

**A:** They remain in the database for audit purposes but are excluded from dashboards and active reports.

### Q: Can I restore an obsolete recommendation?

**A:** Yes, you can manually update the status in the database:
```sql
UPDATE recommendations
SET status = 'pending'
WHERE id = 123;
```

### Q: How often should I run this cleanup?

**A:** Daily is recommended if you frequently make manual changes in AWS Console. Otherwise, weekly is sufficient.

### Q: Does this affect savings tracking?

**A:** No. Historical savings data is preserved. Only future recommendations are affected.

---

## 💡 Best Practices

1. **Always run dry-run first** when testing
2. **Install the cron job** for automated maintenance
3. **Monitor logs** periodically to ensure cleanup is working
4. **Use Wasteless remediator** instead of manual AWS Console changes when possible
5. **Review obsolete recommendations** monthly to understand manual intervention patterns

---

## 🆘 Support

If you encounter issues:

1. Check logs: `logs/cleanup_*.log`
2. Run dry-run mode: `python src/utils/cleanup_orphaned_recommendations.py --dry-run`
3. Verify database connectivity: `docker ps | grep postgres`
4. Check AWS credentials: `aws ec2 describe-instances --max-items 1`

For bugs or feature requests, open an issue on GitHub.
