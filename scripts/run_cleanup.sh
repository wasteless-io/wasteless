#!/bin/bash
###############################################################################
# Wasteless - Automated Cleanup Script for Cron
#
# This script runs the cleanup_orphaned_recommendations.py utility
# and logs the output for monitoring and debugging.
#
# Usage:
#   ./scripts/run_cleanup.sh
#
# Cron example (daily at 3 AM):
#   0 3 * * * /path/to/wasteless/scripts/run_cleanup.sh
#
# Author: Wasteless
###############################################################################

# Exit on error
set -e

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
else
    echo "ERROR: .env file not found at $PROJECT_ROOT/.env"
    exit 1
fi

# Logging configuration
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/cleanup_$(date +%Y%m%d_%H%M%S).log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Start execution
log "======================================================================="
log "Starting Wasteless Cleanup Job"
log "======================================================================="
log "Project Root: $PROJECT_ROOT"
log "Log File: $LOG_FILE"

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

# Run cleanup script
log "Running cleanup script..."
python src/utils/cleanup_orphaned_recommendations.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# Check exit status
if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Cleanup completed successfully"
else
    log "❌ Cleanup failed with exit code $EXIT_CODE"
    exit $EXIT_CODE
fi

# Cleanup old logs (keep last 30 days)
log "Cleaning up old logs (keeping last 30 days)..."
find "$LOG_DIR" -name "cleanup_*.log" -type f -mtime +30 -delete 2>/dev/null || true

log "======================================================================="
log "Cleanup job finished"
log "======================================================================="

exit 0
