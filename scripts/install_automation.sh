#!/bin/bash
###############################################################################
# Wasteless - Complete Automation Installation Script
#
# This script installs all cron jobs for complete automation:
# - CloudWatch metrics collection
# - Waste detection
# - Orphaned recommendations cleanup
#
# Usage:
#   ./scripts/install_automation.sh install   # Install all cron jobs
#   ./scripts/install_automation.sh remove    # Remove all cron jobs
#   ./scripts/install_automation.sh status    # Check installation status
#
# Author: Wasteless
###############################################################################

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Scripts
COLLECTOR_SCRIPT="$SCRIPT_DIR/run_collector.sh"
DETECTOR_SCRIPT="$SCRIPT_DIR/run_detector.sh"
CLEANUP_SCRIPT="$SCRIPT_DIR/run_cleanup.sh"

# Cron identifiers
COLLECTOR_ID="# Wasteless: Automated CloudWatch metrics collection"
DETECTOR_ID="# Wasteless: Automated waste detection"
CLEANUP_ID="# Wasteless: Automated cleanup of orphaned recommendations"

# Function to print colored output
print_info() {
    echo -e "${BLUE}ℹ${NC}  $*"
}

print_success() {
    echo -e "${GREEN}✅${NC} $*"
}

print_warning() {
    echo -e "${YELLOW}⚠️${NC}  $*"
}

print_error() {
    echo -e "${RED}❌${NC} $*"
}

print_header() {
    echo ""
    echo "======================================================================="
    echo "$*"
    echo "======================================================================="
    echo ""
}

# Function to check if a specific cron job is installed
is_installed() {
    local identifier="$1"
    crontab -l 2>/dev/null | grep -F "$identifier" >/dev/null 2>&1
    return $?
}

# Function to install all cron jobs
install_cron_jobs() {
    print_header "🔧 Installing Wasteless Complete Automation"

    # Check if scripts exist
    local missing_scripts=()
    [ ! -f "$COLLECTOR_SCRIPT" ] && missing_scripts+=("run_collector.sh")
    [ ! -f "$DETECTOR_SCRIPT" ] && missing_scripts+=("run_detector.sh")
    [ ! -f "$CLEANUP_SCRIPT" ] && missing_scripts+=("run_cleanup.sh")

    if [ ${#missing_scripts[@]} -ne 0 ]; then
        print_error "Missing scripts: ${missing_scripts[*]}"
        exit 1
    fi

    # Make scripts executable
    chmod +x "$COLLECTOR_SCRIPT" "$DETECTOR_SCRIPT" "$CLEANUP_SCRIPT"

    # Check if any jobs are already installed
    local already_installed=()
    is_installed "$COLLECTOR_ID" && already_installed+=("collector")
    is_installed "$DETECTOR_ID" && already_installed+=("detector")
    is_installed "$CLEANUP_ID" && already_installed+=("cleanup")

    if [ ${#already_installed[@]} -ne 0 ]; then
        print_warning "Some cron jobs are already installed: ${already_installed[*]}"
        read -p "Do you want to reinstall them? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Installation cancelled"
            exit 0
        fi
        # Remove existing jobs
        remove_cron_jobs "silent"
    fi

    # Ask user for schedule preference
    echo "Choose automation schedule:"
    echo ""
    echo "  1) Conservative (Recommended for production)"
    echo "     - Collection: Daily at 2:00 AM"
    echo "     - Detection: Daily at 3:00 AM"
    echo "     - Cleanup:   Daily at 4:00 AM"
    echo ""
    echo "  2) Frequent (Good for testing/development)"
    echo "     - Collection: Every 6 hours (2 AM, 8 AM, 2 PM, 8 PM)"
    echo "     - Detection: Every 6 hours (3 AM, 9 AM, 3 PM, 9 PM)"
    echo "     - Cleanup:   Every 6 hours (4 AM, 10 AM, 4 PM, 10 PM)"
    echo ""
    echo "  3) Very frequent (High AWS API usage)"
    echo "     - Collection: Every 3 hours"
    echo "     - Detection: Every 3 hours"
    echo "     - Cleanup:   Every 3 hours"
    echo ""
    echo "  4) Custom schedules"
    echo ""
    read -p "Enter your choice [1-4]: " schedule_choice

    case $schedule_choice in
        1)
            COLLECTOR_SCHEDULE="0 2 * * *"
            DETECTOR_SCHEDULE="0 3 * * *"
            CLEANUP_SCHEDULE="0 4 * * *"
            SCHEDULE_DESC="Conservative (Daily at 2 AM, 3 AM, 4 AM)"
            ;;
        2)
            COLLECTOR_SCHEDULE="0 2,8,14,20 * * *"
            DETECTOR_SCHEDULE="0 3,9,15,21 * * *"
            CLEANUP_SCHEDULE="0 4,10,16,22 * * *"
            SCHEDULE_DESC="Frequent (Every 6 hours)"
            ;;
        3)
            COLLECTOR_SCHEDULE="0 */3 * * *"
            DETECTOR_SCHEDULE="15 */3 * * *"
            CLEANUP_SCHEDULE="30 */3 * * *"
            SCHEDULE_DESC="Very frequent (Every 3 hours)"
            ;;
        4)
            echo ""
            print_info "Enter cron schedules (format: minute hour day month weekday)"
            read -p "Collector schedule (e.g., '0 2 * * *'): " COLLECTOR_SCHEDULE
            read -p "Detector schedule (e.g., '0 3 * * *'): " DETECTOR_SCHEDULE
            read -p "Cleanup schedule (e.g., '0 4 * * *'): " CLEANUP_SCHEDULE
            SCHEDULE_DESC="Custom schedules"
            ;;
        *)
            print_error "Invalid choice. Exiting."
            exit 1
            ;;
    esac

    # Create cron entries
    COLLECTOR_JOB="$COLLECTOR_SCHEDULE $COLLECTOR_SCRIPT"
    DETECTOR_JOB="$DETECTOR_SCHEDULE $DETECTOR_SCRIPT"
    CLEANUP_JOB="$CLEANUP_SCHEDULE $CLEANUP_SCRIPT"

    # Add to crontab
    (
        crontab -l 2>/dev/null || true
        echo ""
        echo "$COLLECTOR_ID"
        echo "$COLLECTOR_JOB"
        echo ""
        echo "$DETECTOR_ID"
        echo "$DETECTOR_JOB"
        echo ""
        echo "$CLEANUP_ID"
        echo "$CLEANUP_JOB"
    ) | crontab -

    # Verify installation
    local installed_count=0
    is_installed "$COLLECTOR_ID" && ((installed_count++))
    is_installed "$DETECTOR_ID" && ((installed_count++))
    is_installed "$CLEANUP_ID" && ((installed_count++))

    if [ $installed_count -eq 3 ]; then
        print_success "All 3 cron jobs installed successfully!"
        echo ""
        print_info "Schedule: $SCHEDULE_DESC"
        echo ""
        print_info "📊 Jobs installed:"
        echo "  1. CloudWatch Collector: $COLLECTOR_SCHEDULE"
        echo "  2. Waste Detector:       $DETECTOR_SCHEDULE"
        echo "  3. Cleanup:              $CLEANUP_SCHEDULE"
        echo ""
        print_info "📁 Logs will be saved to: $PROJECT_ROOT/logs/"
        echo ""
        print_info "Commands:"
        print_info "  • Check status:  $0 status"
        print_info "  • View logs:     tail -f $PROJECT_ROOT/logs/*.log"
        print_info "  • Remove jobs:   $0 remove"
    else
        print_error "Installation incomplete ($installed_count/3 jobs installed)"
        exit 1
    fi
}

# Function to remove all cron jobs
remove_cron_jobs() {
    local mode="${1:-normal}"

    if [ "$mode" != "silent" ]; then
        print_header "🗑️  Removing Wasteless Automation"
    fi

    # Check if any jobs are installed
    local installed_jobs=()
    is_installed "$COLLECTOR_ID" && installed_jobs+=("collector")
    is_installed "$DETECTOR_ID" && installed_jobs+=("detector")
    is_installed "$CLEANUP_ID" && installed_jobs+=("cleanup")

    if [ ${#installed_jobs[@]} -eq 0 ]; then
        if [ "$mode" != "silent" ]; then
            print_warning "No Wasteless cron jobs found"
        fi
        return 0
    fi

    # Remove all Wasteless entries from crontab
    crontab -l 2>/dev/null | \
        grep -v -F "$COLLECTOR_ID" | \
        grep -v -F "$DETECTOR_ID" | \
        grep -v -F "$CLEANUP_ID" | \
        grep -v -F "$COLLECTOR_SCRIPT" | \
        grep -v -F "$DETECTOR_SCRIPT" | \
        grep -v -F "$CLEANUP_SCRIPT" | \
        crontab -

    # Verify removal
    local remaining_count=0
    is_installed "$COLLECTOR_ID" && ((remaining_count++))
    is_installed "$DETECTOR_ID" && ((remaining_count++))
    is_installed "$CLEANUP_ID" && ((remaining_count++))

    if [ $remaining_count -eq 0 ]; then
        if [ "$mode" != "silent" ]; then
            print_success "All Wasteless cron jobs removed successfully"
        fi
    else
        print_error "Failed to remove all jobs ($remaining_count remaining)"
        exit 1
    fi
}

# Function to check status
check_status() {
    print_header "📊 Wasteless Automation Status"

    local installed_count=0

    # Check collector
    if is_installed "$COLLECTOR_ID"; then
        print_success "CloudWatch Collector: INSTALLED"
        ((installed_count++))
    else
        print_warning "CloudWatch Collector: NOT INSTALLED"
    fi

    # Check detector
    if is_installed "$DETECTOR_ID"; then
        print_success "Waste Detector: INSTALLED"
        ((installed_count++))
    else
        print_warning "Waste Detector: NOT INSTALLED"
    fi

    # Check cleanup
    if is_installed "$CLEANUP_ID"; then
        print_success "Cleanup: INSTALLED"
        ((installed_count++))
    else
        print_warning "Cleanup: NOT INSTALLED"
    fi

    echo ""

    if [ $installed_count -eq 3 ]; then
        print_success "Full automation is ACTIVE ($installed_count/3 jobs)"
        echo ""
        print_info "Current crontab entries:"
        echo "---"
        crontab -l 2>/dev/null | grep -A 1 "# Wasteless:"
        echo "---"
    elif [ $installed_count -gt 0 ]; then
        print_warning "Partial automation ($installed_count/3 jobs installed)"
        print_info "Run '$0 install' to complete installation"
    else
        print_warning "Automation is NOT installed"
        print_info "Run '$0 install' to set it up"
    fi

    # Show recent logs
    echo ""
    print_info "Recent log files:"
    if [ -d "$PROJECT_ROOT/logs" ]; then
        ls -lt "$PROJECT_ROOT/logs/"*.log 2>/dev/null | head -10 || print_info "No logs found yet"
    else
        print_info "Logs directory not created yet (will be created on first run)"
    fi
}

# Main execution
case "${1:-}" in
    install)
        install_cron_jobs
        ;;
    remove)
        remove_cron_jobs
        ;;
    status)
        check_status
        ;;
    *)
        echo "Wasteless - Complete Automation Management"
        echo ""
        echo "Usage: $0 {install|remove|status}"
        echo ""
        echo "Commands:"
        echo "  install  - Install complete automation (collector + detector + cleanup)"
        echo "  remove   - Remove all cron jobs"
        echo "  status   - Check installation status and view logs"
        echo ""
        echo "What gets automated:"
        echo "  • CloudWatch metrics collection (AWS API)"
        echo "  • EC2 idle waste detection"
        echo "  • Orphaned recommendations cleanup"
        echo ""
        exit 1
        ;;
esac

exit 0
