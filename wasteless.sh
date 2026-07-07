#!/bin/bash
#
# wasteless — WasteLess CLI
#
# Usage:
#   wasteless             Start the web UI in background (default)
#   wasteless stop        Stop the web UI
#   wasteless logs        View server logs (tail -f)
#   wasteless status      Check if server is running
#   wasteless collect     Collect AWS metrics + detect idle instances
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

PID_FILE="$HOME/.wasteless.pid"
LOG_FILE="$HOME/.wasteless.log"
COLLECTOR_PID_FILE="$HOME/.wasteless-collector.pid"
# A directory, not a file: mkdir is atomic (POSIX), so concurrent
# `wasteless.sh collect` invocations racing on this can't both "win" the
# check like a plain [ -f lockfile ] + echo $$ > lockfile pattern would
# (that had a TOCTOU window where two overlapping runs could both pass
# the check before either wrote its PID -- confirmed by launching 5
# concurrent collects, 2 got through instead of 1).
COLLECT_LOCK_DIR="$HOME/.wasteless-collect.lock.d"

CMD="${1:-start}"

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
_start() {
    cd "$SCRIPT_DIR/ui"

    if [ ! -f .env ]; then
        echo -e "${RED}[ERROR]${NC} ui/.env not found. Run ./install.sh first."
        exit 1
    fi
    source .env

    if [ -d "venv" ]; then
        source venv/bin/activate
    elif [ -d ".venv" ]; then
        source .venv/bin/activate
    else
        echo -e "${RED}[ERROR]${NC} Virtual environment not found in ui/. Run ./install.sh first."
        exit 1
    fi

    PORT="${WASTELESS_PORT:-${STREAMLIT_SERVER_PORT:-8888}}"

    # Already running?
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}WasteLess is already running → http://localhost:$PORT${NC}"
        exit 0
    fi

    # Port taken by something else?
    if lsof -ti:"$PORT" > /dev/null 2>&1; then
        PID=$(lsof -ti:"$PORT" | head -1)
        PROC=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        echo -e "${YELLOW}Port $PORT is already in use by '$PROC' (PID $PID).${NC}"
        echo "To use a different port: WASTELESS_PORT=8889 wasteless"
        exit 1
    fi

    _ensure_cron

    echo ""
    echo -e "  ${YELLOW}Starting WasteLess...${NC}"
    echo ""

    export PYTHONUNBUFFERED=1
    nohup uvicorn main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_FILE" 2>&1 &
    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    disown "$UVICORN_PID"

    # Inline spinner — waits in the foreground up to 30s so nothing prints after the prompt
    SPIN=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    SLEN=${#SPIN[@]}
    i=0
    while [ $i -lt 30 ]; do
        if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
            printf "\r  ${GREEN}✓ Ready → http://localhost:%s${NC}                    \n" "$PORT"
            echo ""
            echo -e "  ${CYAN}wasteless logs${NC}     View server logs"
            echo -e "  ${CYAN}wasteless stop${NC}     Stop the server"
            echo ""
            command -v open &>/dev/null && open "http://localhost:$PORT"
            command -v xdg-open &>/dev/null && xdg-open "http://localhost:$PORT" &>/dev/null
            return 0
        fi
        printf "\r  %s  Starting up... (%ds)" "${SPIN[$((i % SLEN))]}" "$i"
        sleep 1
        i=$((i + 1))
    done

    # Still not ready — hand off cleanly, no background terminal output
    printf "\r  ${YELLOW}⚠  Still starting — first run may take a minute${NC}         \n"
    echo ""
    echo -e "  PID ${UVICORN_PID}"
    echo -e "  ${CYAN}wasteless logs${NC}     View server logs"
    echo -e "  ${CYAN}wasteless stop${NC}     Stop the server"
    echo ""

    # Background: open browser silently when ready (no terminal output)
    (
        j=$i
        while [ $j -lt 120 ]; do
            sleep 1
            if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
                command -v open &>/dev/null && open "http://localhost:$PORT"
                command -v xdg-open &>/dev/null && xdg-open "http://localhost:$PORT" &>/dev/null
                exit 0
            fi
            j=$((j + 1))
        done
    ) &
    disown $!
}

# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------
_stop() {
    # Stop collector loop
    if [ -f "$COLLECTOR_PID_FILE" ]; then
        CPID=$(cat "$COLLECTOR_PID_FILE")
        kill "$CPID" 2>/dev/null
        rm -f "$COLLECTOR_PID_FILE"
    fi
    rm -rf "$COLLECT_LOCK_DIR"

    STOPPED=0
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            STOPPED=1
        fi
        rm -f "$PID_FILE"
    fi

    # Tuer tous les process encore sur le port (enfants uvicorn --reload)
    PORT="${WASTELESS_PORT:-8888}"
    PORT_PIDS=$(lsof -ti:"$PORT" 2>/dev/null)
    if [ -n "$PORT_PIDS" ]; then
        echo "$PORT_PIDS" | xargs kill 2>/dev/null
        STOPPED=1
    fi

    if [ "$STOPPED" -eq 1 ]; then
        echo -e "${GREEN}WasteLess stopped${NC}"
    else
        echo "WasteLess is not running"
    fi
}

# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file found at $LOG_FILE"
    fi
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
_status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        PID=$(cat "$PID_FILE")
        PORT=$(lsof -p "$PID" -i -a 2>/dev/null | awk '/LISTEN/{match($9,/:([0-9]+)/,a); if(a[1]) print a[1]}' | head -1)
        PORT="${PORT:-${WASTELESS_PORT:-8888}}"
        echo -e "${GREEN}Running${NC} — PID $PID → http://localhost:$PORT"
    else
        echo "Not running"
        [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
    fi
}

# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
_collect() {
    # Prevent concurrent runs. mkdir is atomic: only one racing invocation
    # can create the directory, so there's no check-then-write window.
    if ! mkdir "$COLLECT_LOCK_DIR" 2>/dev/null; then
        LOCK_PID=$(cat "$COLLECT_LOCK_DIR/pid" 2>/dev/null)
        if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
            return 0
        fi
        # Stale lock (holder died without cleaning up, e.g. kill -9) --
        # reclaim it. If another process wins this second race, that's
        # fine: we just yield to them instead of double-running.
        rm -rf "$COLLECT_LOCK_DIR"
        if ! mkdir "$COLLECT_LOCK_DIR" 2>/dev/null; then
            return 0
        fi
    fi
    echo $$ > "$COLLECT_LOCK_DIR/pid"
    trap 'rm -rf "$COLLECT_LOCK_DIR"' EXIT INT TERM

    cd "$SCRIPT_DIR"

    if [ ! -d "venv" ]; then
        echo -e "${RED}[ERROR]${NC} Virtual environment not found. Run ./install.sh first."
        exit 1
    fi

    source venv/bin/activate

    echo ""
    echo -e "${BOLD}WasteLess — Collect & Detect${NC}"
    echo ""

    _run_step() {
        local label="$1" cmd="$2"
        echo -e "$label"
        if python3 $cmd; then
            echo -e "${GREEN}[OK]${NC} Done"
        else
            echo -e "${YELLOW}[WARN]${NC} Step failed — continuing"
        fi
        echo ""
    }

    _run_step "${CYAN}[1/10]${NC} Collecting CloudWatch metrics..."    "src/collectors/aws_cloudwatch.py"
    _run_step "${CYAN}[2/10]${NC} Detecting idle EC2 instances..."     "src/detectors/ec2_idle.py"
    _run_step "${CYAN}[3/10]${NC} Detecting stopped EC2 instances..."  "src/detectors/ec2_stopped.py"
    _run_step "${CYAN}[4/10]${NC} Detecting orphaned EBS volumes..."   "src/detectors/ebs_orphan.py"
    _run_step "${CYAN}[5/10]${NC} Detecting unassociated Elastic IPs..." "src/detectors/eip_orphan.py"
    _run_step "${CYAN}[6/10]${NC} Detecting old EBS snapshots..."      "src/detectors/snapshot_orphan.py"

    # Steps 7-10 need the steampipe CLI (brew install turbot/tap/steampipe
    # && steampipe plugin install aws). Skip them with one clear message
    # instead of four confusing per-step failures when it's absent.
    if command -v steampipe &> /dev/null; then
        _run_step "${CYAN}[7/10]${NC} Detecting unused load balancers (Steampipe)..."   "src/detectors/elb_unused.py"
        _run_step "${CYAN}[8/10]${NC} Detecting unused NAT gateways (Steampipe)..."     "src/detectors/nat_gateway_unused.py"
        _run_step "${CYAN}[9/10]${NC} Detecting unused VPCs (Steampipe)..."             "src/detectors/vpc_unused.py"
        _run_step "${CYAN}[10/10]${NC} Detecting gp2→gp3 migration candidates (Steampipe)..." "src/detectors/ebs_gp2_migration.py"
        _SKIPPED_STEPS=""
    else
        echo -e "${YELLOW}[WARN]${NC} steampipe CLI not found — skipping steps 7-10"
        echo "  (unused load balancers, NAT gateways, VPCs, gp2 migration)."
        echo "  Install with: brew install turbot/tap/steampipe && steampipe plugin install aws"
        echo ""
        _SKIPPED_STEPS="elb_unused,nat_gateway_unused,vpc_unused,ebs_gp2_migration"
    fi

    # Record the run so the UI can flag a partial collection instead of
    # silently under-reporting waste — the steampipe warning above only
    # ever reached ~/.wasteless.log, never the dashboard.
    WASTELESS_SKIPPED_STEPS="$_SKIPPED_STEPS" python3 -c "
import os
import sys
sys.path.insert(0, 'src')
from core.database import execute_query
skipped = [s for s in os.environ.get('WASTELESS_SKIPPED_STEPS', '').split(',') if s]
execute_query(
    'INSERT INTO collection_runs (full_run, skipped_steps) VALUES (%s, %s)',
    (len(skipped) == 0, skipped),
)
" 2>>"$LOG_FILE" || echo -e "${YELLOW}[WARN]${NC} could not record collection_runs status"

    echo ""
    echo -e "${GREEN}Done!${NC} Open ${BOLD}http://localhost:8888/recommendations${NC} to review."
    echo ""
}

# ---------------------------------------------------------------------------
# ensure_collector  (called automatically on start)
# ---------------------------------------------------------------------------
_ensure_cron() {
    # Already running?
    if [ -f "$COLLECTOR_PID_FILE" ] && kill -0 "$(cat "$COLLECTOR_PID_FILE")" 2>/dev/null; then
        return
    fi

    (
        SELF="$SCRIPT_DIR/wasteless.sh"
        while true; do
            "$SELF" collect >> "$LOG_FILE" 2>&1
            sleep 300
        done
    ) &
    echo $! > "$COLLECTOR_PID_FILE"
    disown $!
    echo -e "  ${CYAN}Auto-collection started (every 5 min)${NC}"
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
case "$CMD" in
    "" | start)   _start   ;;
    stop)         _stop    ;;
    logs)         _logs    ;;
    status)       _status  ;;
    collect)      _collect ;;
    *)
        echo -e "${BOLD}Usage:${NC} wasteless [command]"
        echo ""
        echo "Commands:"
        echo "  start     Start the web UI in background (default)"
        echo "  stop      Stop the web UI"
        echo "  logs      View server logs (tail -f)"
        echo "  status    Check if server is running"
        echo "  collect   Collect AWS metrics and detect idle instances"
        echo ""
        exit 1
        ;;
esac
