#!/bin/bash
###############################################################################
# Wasteless - Automated CloudWatch Metrics Collection Script
#
# This script runs the AWS CloudWatch collector and logs the output.
#
# Usage:
#   ./scripts/run_collector.sh
#
# Cron example (daily at 2 AM):
#   0 2 * * * /path/to/wasteless/scripts/run_collector.sh
#
# Author: Wasteless
###############################################################################

# Exit on error
set -e

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Ensure .env exists — the Python entrypoint loads it itself (load_dotenv),
# so the shell must NOT `source` it: a password containing $, ` or a space
# would break the run or execute arbitrary shell (same reasoning as
# get_env_var in install.sh). Only AWS_REGION is read here, for the log.
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "ERROR: .env file not found at $PROJECT_ROOT/.env"
    exit 1
fi
AWS_REGION="$(grep -E '^AWS_REGION=' "$PROJECT_ROOT/.env" | tail -n1 | cut -d= -f2-)"

# Logging configuration
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/collector_$(date +%Y%m%d_%H%M%S).log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Start execution
log "======================================================================="
log "Starting Wasteless CloudWatch Metrics Collection"
log "======================================================================="
log "Project Root: $PROJECT_ROOT"
log "Log File: $LOG_FILE"
log "AWS Region: ${AWS_REGION:-not set}"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    log "Activating virtual environment..."
    source "$PROJECT_ROOT/venv/bin/activate"
else
    log "ERROR: Virtual environment not found at $PROJECT_ROOT/venv"
    exit 1
fi

# Navigate to project root
cd "$PROJECT_ROOT"

# Run collector
log "Running CloudWatch collector..."
python src/collectors/aws_cloudwatch.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# Check exit status
if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Metrics collection completed successfully"
else
    log "❌ Metrics collection failed with exit code $EXIT_CODE"
    exit $EXIT_CODE
fi

# Cleanup old logs (keep last 30 days)
log "Cleaning up old logs (keeping last 30 days)..."
find "$LOG_DIR" -name "collector_*.log" -type f -mtime +30 -delete 2>/dev/null || true

log "======================================================================="
log "Collection job finished"
log "======================================================================="

exit 0
