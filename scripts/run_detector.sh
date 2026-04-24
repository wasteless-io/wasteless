#!/bin/bash
###############################################################################
# Wasteless - Automated Waste Detection Script
#
# This script runs the EC2 idle detector and logs the output.
#
# Usage:
#   ./scripts/run_detector.sh
#
# Cron example (daily at 3 AM):
#   0 3 * * * /path/to/wasteless/scripts/run_detector.sh
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
LOG_FILE="$LOG_DIR/detector_$(date +%Y%m%d_%H%M%S).log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Start execution
log "======================================================================="
log "Starting Wasteless Waste Detection"
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

# Run detector
log "Running EC2 idle detector..."
python src/detectors/ec2_idle.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# Check exit status
if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Waste detection completed successfully"
else
    log "❌ Waste detection failed with exit code $EXIT_CODE"
    exit $EXIT_CODE
fi

# Cleanup old logs (keep last 30 days)
log "Cleaning up old logs (keeping last 30 days)..."
find "$LOG_DIR" -name "detector_*.log" -type f -mtime +30 -delete 2>/dev/null || true

log "======================================================================="
log "Detection job finished"
log "======================================================================="

exit 0
