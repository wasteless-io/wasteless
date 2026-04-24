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
COLLECT_LOCK_FILE="$HOME/.wasteless-collect.lock"

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
    echo -e "  ${YELLOW}Starting WasteLess in background...${NC}"
    echo ""

    export PYTHONUNBUFFERED=1
    nohup uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload \
        --reload-exclude 'venv/**' \
        --reload-exclude '*.pyc' \
        --reload-exclude '__pycache__/**' >> "$LOG_FILE" 2>&1 &
    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    disown "$UVICORN_PID"

    echo -e "  PID ${UVICORN_PID} — browser will open automatically when ready."
    echo ""
    echo -e "  ${CYAN}wasteless logs${NC}     View server logs"
    echo -e "  ${CYAN}wasteless stop${NC}     Stop the server"
    echo ""

    # Background watcher: open browser when server responds
    (
        i=0
        while [ $i -lt 120 ]; do
            sleep 1
            if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
                printf "  \033[0;32m✅ Ready → http://localhost:%s\033[0m\n\n" "$PORT"
                command -v open &>/dev/null && open "http://localhost:$PORT"
                command -v xdg-open &>/dev/null && xdg-open "http://localhost:$PORT" &>/dev/null
                exit 0
            fi
            i=$((i + 1))
            if [ $i -eq 30 ]; then
                printf "  \033[1;33mStill starting... (first run may take a minute)\033[0m\n"
            fi
        done
        printf "  \033[1;33m⚠  Server did not respond after 120s — run: wasteless logs\033[0m\n"
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
    rm -f "$COLLECT_LOCK_FILE"

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm -f "$PID_FILE"
            echo -e "${GREEN}WasteLess stopped (PID $PID)${NC}"
        else
            echo "WasteLess is not running"
            rm -f "$PID_FILE"
        fi
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
    # Prevent concurrent runs
    if [ -f "$COLLECT_LOCK_FILE" ]; then
        LOCK_PID=$(cat "$COLLECT_LOCK_FILE" 2>/dev/null)
        if kill -0 "$LOCK_PID" 2>/dev/null; then
            return 0
        fi
    fi
    echo $$ > "$COLLECT_LOCK_FILE"
    trap 'rm -f "$COLLECT_LOCK_FILE"' EXIT INT TERM

    cd "$SCRIPT_DIR"

    if [ ! -d "venv" ]; then
        echo -e "${RED}[ERROR]${NC} Virtual environment not found. Run ./install.sh first."
        exit 1
    fi

    source venv/bin/activate

    echo ""
    echo -e "${BOLD}WasteLess — Collect & Detect${NC}"
    echo ""

    echo -e "${CYAN}[1/6]${NC} Collecting CloudWatch metrics..."
    if python3 src/collectors/aws_cloudwatch.py; then
        echo -e "${GREEN}[OK]${NC} Metrics collected"
    else
        echo -e "${RED}[ERROR]${NC} Collector failed"
        exit 1
    fi

    echo ""
    echo -e "${CYAN}[2/6]${NC} Detecting idle EC2 instances..."
    if python3 src/detectors/ec2_idle.py; then
        echo -e "${GREEN}[OK]${NC} Detection complete"
    else
        echo -e "${RED}[ERROR]${NC} Detector failed"
        exit 1
    fi

    echo ""
    echo -e "${CYAN}[3/6]${NC} Detecting stopped EC2 instances..."
    if python3 src/detectors/ec2_stopped.py; then
        echo -e "${GREEN}[OK]${NC} Detection complete"
    else
        echo -e "${RED}[ERROR]${NC} Detector failed"
        exit 1
    fi

    echo ""
    echo -e "${CYAN}[4/6]${NC} Detecting orphaned EBS volumes..."
    if python3 src/detectors/ebs_orphan.py; then
        echo -e "${GREEN}[OK]${NC} Detection complete"
    else
        echo -e "${RED}[ERROR]${NC} Detector failed"
        exit 1
    fi

    echo ""
    echo -e "${CYAN}[5/6]${NC} Detecting unassociated Elastic IPs..."
    if python3 src/detectors/eip_orphan.py; then
        echo -e "${GREEN}[OK]${NC} Detection complete"
    else
        echo -e "${RED}[ERROR]${NC} Detector failed"
        exit 1
    fi

    echo ""
    echo -e "${CYAN}[6/6]${NC} Detecting old EBS snapshots..."
    if python3 src/detectors/snapshot_orphan.py; then
        echo -e "${GREEN}[OK]${NC} Detection complete"
    else
        echo -e "${RED}[ERROR]${NC} Detector failed"
        exit 1
    fi

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
        while true; do
            "$SCRIPT_DIR/wasteless.sh" collect >> "$LOG_FILE" 2>&1
            sleep 5
        done
    ) &
    echo $! > "$COLLECTOR_PID_FILE"
    disown $!
    echo -e "  ${CYAN}Auto-collection started (every 5s)${NC}"
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
