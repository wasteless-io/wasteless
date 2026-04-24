# Automation Guide - Wasteless

## 📌 Overview

This guide explains how to set up complete automation for Wasteless, eliminating the need for manual execution of collection, detection, and cleanup tasks.

## 🎯 What Gets Automated

When you install automation, the following tasks run automatically on a schedule:

1. **CloudWatch Metrics Collection** (`run_collector.sh`)
   - Fetches CPU, network, and instance metadata from AWS
   - Stores data in PostgreSQL
   - Runs every day (or more frequently based on your choice)

2. **Waste Detection** (`run_detector.sh`)
   - Analyzes collected metrics to identify idle instances
   - Generates recommendations (terminate/stop/downsize)
   - Creates actionable insights for cost savings

3. **Orphaned Recommendations Cleanup** (`run_cleanup.sh`)
   - Synchronizes database with actual AWS state
   - Marks recommendations as obsolete when instances are deleted
   - Keeps dashboards accurate and up-to-date

## 🚀 Quick Start

### Install Complete Automation

```bash
# Run the installer
./scripts/install_automation.sh install

# Follow the interactive prompts to choose your schedule
```

### Check Status

```bash
./scripts/install_automation.sh status
```

### Remove Automation

```bash
./scripts/install_automation.sh remove
```

---

## 📅 Schedule Options

When installing, you'll be prompted to choose a schedule:

### Option 1: Conservative (Recommended for Production)

**Best for:** Production environments, cost-conscious AWS usage

```
Collection: Daily at 2:00 AM
Detection:  Daily at 3:00 AM
Cleanup:    Daily at 4:00 AM
```

**Pros:**
- Minimal AWS API calls
- Low cost
- Sufficient for most use cases
- Runs during off-peak hours

**Cons:**
- 24-hour delay to detect new waste
- Less responsive to changes

### Option 2: Frequent (Good for Testing)

**Best for:** Development, testing, active optimization periods

```
Collection: Every 6 hours (2 AM, 8 AM, 2 PM, 8 PM)
Detection:  Every 6 hours (3 AM, 9 AM, 3 PM, 9 PM)
Cleanup:    Every 6 hours (4 AM, 10 AM, 4 PM, 10 PM)
```

**Pros:**
- Faster detection of waste
- More up-to-date recommendations
- Good for testing automation

**Cons:**
- 4x more AWS API calls
- Higher CloudWatch API costs

### Option 3: Very Frequent (High AWS Usage)

**Best for:** Large teams, frequent infrastructure changes, demo environments

```
Collection: Every 3 hours
Detection:  Every 3 hours (15 min offset)
Cleanup:    Every 3 hours (30 min offset)
```

**Pros:**
- Near real-time waste detection
- Always up-to-date dashboards

**Cons:**
- 8x more AWS API calls
- Significant CloudWatch API costs
- Overkill for most use cases

### Option 4: Custom Schedules

Define your own cron schedules for each task.

**Example custom schedules:**

```bash
# Collection: Monday-Friday at 6 AM
0 6 * * 1-5

# Detection: Twice daily (morning and evening)
0 8,20 * * *

# Cleanup: Weekly on Sunday at midnight
0 0 * * 0
```

---

## 🛠️ Advanced Usage

### Run Individual Tasks Manually

Even with automation installed, you can run tasks manually:

```bash
# Run collection manually
./scripts/run_collector.sh

# Run detection manually
./scripts/run_detector.sh

# Run cleanup manually
./scripts/run_cleanup.sh

# Or use Python directly
python src/collectors/aws_cloudwatch.py
python src/detectors/ec2_idle.py
python src/utils/cleanup_orphaned_recommendations.py
```

### View Logs

```bash
# View all logs
ls -lt logs/

# Monitor collector in real-time
tail -f logs/collector_*.log

# Monitor detector in real-time
tail -f logs/detector_*.log

# Monitor cleanup in real-time
tail -f logs/cleanup_*.log

# View today's logs
cat logs/*_$(date +%Y%m%d)_*.log
```

### Customize Schedules After Installation

```bash
# Edit crontab directly
crontab -e

# Find Wasteless entries (they have comments)
# Modify the schedule, save, and exit
```

### Check Cron Execution

```bash
# View cron system logs (macOS)
tail -f /var/log/cron.log

# View cron system logs (Linux)
tail -f /var/log/syslog | grep CRON

# Check if cron daemon is running (macOS)
sudo launchctl list | grep cron

# Check if cron daemon is running (Linux)
sudo systemctl status cron
```

---

## 📊 Understanding the Pipeline

### Execution Order

The tasks are scheduled with time offsets to run sequentially:

```
2:00 AM - Collection starts
   ↓
   [Fetches metrics from AWS CloudWatch]
   ↓
2:05 AM - Collection completes
   ↓
3:00 AM - Detection starts
   ↓
   [Analyzes metrics, generates recommendations]
   ↓
3:01 AM - Detection completes
   ↓
4:00 AM - Cleanup starts
   ↓
   [Syncs with AWS, marks obsolete recommendations]
   ↓
4:01 AM - Cleanup completes
```

### Data Flow

```
┌─────────────────────────────────────────────┐
│          AWS Account (Your Cloud)           │
│   CloudWatch API  │  EC2 API  │  Cost API   │
└──────────────┬──────────────────────────────┘
               │
               │ [Automated Collection - 2 AM]
               ↓
┌─────────────────────────────────────────────┐
│              PostgreSQL Database            │
│  ec2_metrics  │  waste_detected  │  etc.    │
└──────────────┬──────────────────────────────┘
               │
               │ [Automated Detection - 3 AM]
               ↓
┌─────────────────────────────────────────────┐
│           Recommendations Table             │
│   status: pending  │  applied  │  obsolete  │
└──────────────┬──────────────────────────────┘
               │
               │ [Automated Cleanup - 4 AM]
               ↓
┌─────────────────────────────────────────────┐
│      Frontend (Streamlit / Metabase)        │
│         Shows up-to-date insights           │
└─────────────────────────────────────────────┘
```

---

## 🔍 Monitoring Automation

### Check Last Execution Time

```bash
./scripts/install_automation.sh status
```

This shows:
- Installation status (installed/not installed)
- Current cron schedules
- Recent log files with timestamps

### Verify Tasks Ran Successfully

```bash
# Check exit codes in logs
grep "completed successfully" logs/collector_*.log | tail -5
grep "completed successfully" logs/detector_*.log | tail -5
grep "completed successfully" logs/cleanup_*.log | tail -5

# Check for errors
grep "ERROR" logs/*.log | tail -20
grep "failed" logs/*.log | tail -20
```

### Monitor Database Growth

```bash
# Check metrics count over time
docker exec -it wasteless-postgres psql -U wasteless -d wasteless -c \
  "SELECT collection_date, COUNT(*) as metrics_count
   FROM ec2_metrics
   GROUP BY collection_date
   ORDER BY collection_date DESC
   LIMIT 10;"

# Check recommendations by date
docker exec -it wasteless-postgres psql -U wasteless -d wasteless -c \
  "SELECT w.detection_date, COUNT(*) as recommendations
   FROM waste_detected w
   JOIN recommendations r ON w.id = r.waste_id
   WHERE r.status = 'pending'
   GROUP BY w.detection_date
   ORDER BY w.detection_date DESC;"
```

---

## 🔧 Troubleshooting

### Issue: Cron jobs not running

**Symptoms:** No new logs being created

**Solutions:**

```bash
# 1. Check if cron daemon is running (macOS)
sudo launchctl list | grep cron

# 2. Check if cron daemon is running (Linux)
sudo systemctl status cron

# 3. Verify crontab entries exist
crontab -l | grep Wasteless

# 4. Check script permissions
ls -l scripts/*.sh

# 5. Test script manually
./scripts/run_collector.sh
```

### Issue: Scripts failing with "venv not found"

**Symptoms:** Log shows "ERROR: Virtual environment not found"

**Solution:**

```bash
# Verify venv exists
ls -la venv/

# Recreate if missing
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Issue: AWS credentials error

**Symptoms:** Log shows "Unable to locate credentials" or "InvalidClientTokenId"

**Solution:**

```bash
# 1. Check .env file
cat .env | grep AWS_

# 2. Verify credentials work
aws ec2 describe-instances --max-items 1

# 3. Check if environment is loaded in script
# Scripts automatically source .env
```

### Issue: Database connection error

**Symptoms:** Log shows "could not connect to server"

**Solution:**

```bash
# 1. Check if PostgreSQL is running
docker ps | grep postgres

# 2. Restart PostgreSQL
docker-compose restart postgres

# 3. Verify connection manually
docker exec -it wasteless-postgres psql -U wasteless -d wasteless -c "SELECT 1;"
```

### Issue: Too many logs filling disk

**Symptoms:** Large number of log files, disk space warning

**Solution:**

```bash
# Scripts auto-clean logs older than 30 days
# To clean manually:
find logs/ -name "*.log" -type f -mtime +30 -delete

# To reduce retention period, edit scripts:
# Change "-mtime +30" to "-mtime +7" (7 days)
```

---

## 💡 Best Practices

### For Production

1. **Use Conservative schedule** (daily)
2. **Monitor logs weekly** for errors
3. **Set up disk space alerts** for logs directory
4. **Review recommendations regularly** via frontend
5. **Document any manual changes** in AWS Console

### For Development/Testing

1. **Use Frequent schedule** (every 6 hours)
2. **Test automation with dry-run first**
3. **Monitor logs daily** during testing
4. **Validate data accuracy** against AWS Console
5. **Use separate AWS account** if possible

### General Recommendations

1. **Always install automation** - Don't rely on manual execution
2. **Keep scripts updated** - Pull latest changes regularly
3. **Review CloudWatch costs** - Monitor AWS bill for API usage
4. **Set up Slack/Email alerts** for critical errors (future feature)
5. **Document your schedule** - Note why you chose specific timings

---

## 📚 Related Documentation

- [Cleanup Guide](CLEANUP_GUIDE.md) - Details on orphaned recommendations cleanup
- [Main README](../README.md) - Installation and setup
- [Configuration](../config/remediation.yaml) - Safeguard settings

---

## 🔐 Security Considerations

### Cron Environment

Cron jobs run with limited environment variables. Our scripts handle this by:
- Explicitly sourcing `.env` file
- Loading all required configuration
- Validating credentials before execution

### Credentials Safety

- Scripts never log credentials
- AWS credentials stored only in `.env` (gitignored)
- Uses boto3 credential chain (IAM roles supported)

### Log Security

- Logs may contain instance IDs and metadata
- Logs stored locally in `logs/` (gitignored)
- No sensitive data logged (credentials, API keys)

---

## 💰 Cost Considerations

### AWS API Costs

Automation increases AWS API calls. Estimated costs per month:

**Conservative Schedule (Daily):**
- CloudWatch API: ~$0.01/month
- EC2 API: ~$0.00/month (free tier)
- **Total: ~$1/month**

**Frequent Schedule (Every 6 hours):**
- CloudWatch API: ~$0.04/month
- EC2 API: ~$0.00/month (free tier)
- **Total: ~$4/month**

**Very Frequent Schedule (Every 3 hours):**
- CloudWatch API: ~$0.08/month
- EC2 API: ~$0.01/month
- **Total: ~$9/month**

**Note:** Costs vary based on number of instances and metrics. The savings from waste detection far outweigh these costs.

---

## ❓ FAQ

### Q: Can I run automation on multiple AWS accounts?

**A:** Yes, but you need separate installations:
1. Clone the repo once per account
2. Configure each `.env` with different credentials
3. Install cron jobs for each installation
4. Use different log directories if on same server

### Q: What happens if two jobs run simultaneously?

**A:** Scripts use PostgreSQL advisory locks to prevent concurrent execution. If a job is already running, the new one will skip execution.

### Q: Can I pause automation temporarily?

**A:** Yes, two options:
```bash
# Option 1: Remove cron jobs (clean uninstall)
./scripts/install_automation.sh remove

# Option 2: Comment out in crontab (faster to re-enable)
crontab -e
# Add # before each Wasteless line
```

### Q: How do I change the schedule after installation?

**A:** Two options:
```bash
# Option 1: Reinstall (easiest)
./scripts/install_automation.sh remove
./scripts/install_automation.sh install

# Option 2: Edit crontab manually
crontab -e
```

### Q: Does automation work with Docker deployment?

**A:** Yes, but cron must be installed on the host machine (not in container). Scripts connect to containerized PostgreSQL.

---

## 🆘 Support

If you encounter issues with automation:

1. Check logs: `ls -lt logs/`
2. Verify status: `./scripts/install_automation.sh status`
3. Test manually: `./scripts/run_collector.sh`
4. Review crontab: `crontab -l`
5. Open GitHub issue with logs attached

---

## 🔄 Updating Automation

When you update Wasteless code:

```bash
# Pull latest changes
git pull origin main

# Scripts are automatically updated
# Cron jobs will use new scripts on next execution

# To force immediate update, reinstall:
./scripts/install_automation.sh remove
./scripts/install_automation.sh install
```

---

**Automation Status:** ✅ Production Ready
**Supported Systems:** macOS, Linux (cron-based)
**Windows Support:** Use Task Scheduler (manual configuration required)
