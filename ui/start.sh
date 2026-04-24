#!/bin/bash
#
# WasteLess UI - Start Script (FastAPI)
#
# Usage: ./start.sh
#
# To create a 'wasteless' alias, run:
#   source start.sh --install-alias
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Handle --install-alias flag
if [ "$1" = "--install-alias" ]; then
    SHELL_RC=""
    if [ -n "$ZSH_VERSION" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ]; then
        SHELL_RC="$HOME/.bashrc"
    fi

    if [ -n "$SHELL_RC" ]; then
        ALIAS_LINE="alias wasteless='$SCRIPT_DIR/start.sh'"

        # Check if alias already exists
        if grep -q "alias wasteless=" "$SHELL_RC" 2>/dev/null; then
            echo "Alias 'wasteless' already exists in $SHELL_RC"
        else
            echo "" >> "$SHELL_RC"
            echo "# WasteLess CLI" >> "$SHELL_RC"
            echo "$ALIAS_LINE" >> "$SHELL_RC"
            echo "Alias 'wasteless' added to $SHELL_RC"
            echo "Run 'source $SHELL_RC' or open a new terminal to use it."
        fi
    else
        echo "Could not detect shell. Add this manually to your shell config:"
        echo "  alias wasteless='$SCRIPT_DIR/start.sh'"
    fi
    exit 0
fi

cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  __        __        _       _                "
echo "  \ \      / /_ _ ___| |_ ___| | ___  ___ ___ "
echo "   \ \ /\ / / _\` / __| __/ _ \ |/ _ \/ __/ __|"
echo "    \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \\"
echo "     \_/\_/ \__,_|___/\__\___|_|\___||___/___/"
echo ""
echo "       Cloud Cost Optimization Platform"
echo -e "${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found!${NC}"
    echo "Please create a .env file with database credentials"
    echo "You can copy from: cp .env.template .env"
    exit 1
fi

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
fi

# Load environment
source .env

# Get port (WASTELESS_PORT env var overrides .env setting)
PORT="${WASTELESS_PORT:-${STREAMLIT_SERVER_PORT:-8888}}"

# Check if port is in use
if lsof -ti:$PORT > /dev/null 2>&1; then
    PID=$(lsof -ti:$PORT | head -1)
    PROC=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
    if echo "$PROC" | grep -qE "uvicorn|python"; then
        echo -e "${GREEN}WasteLess is already running on http://localhost:$PORT${NC}"
        echo "Open your browser at: http://localhost:$PORT"
        exit 0
    else
        echo -e "${YELLOW}Port $PORT is already in use by '$PROC' (PID $PID).${NC}"
        echo "To use a different port:"
        echo "  WASTELESS_PORT=8889 wasteless"
        exit 1
    fi
fi

echo ""
echo -e "  ${YELLOW}Starting server... (Press Ctrl+C to stop)${NC}"
echo ""

# Background watcher: polls until server responds, then opens browser
(
    i=0
    while [ $i -lt 120 ]; do
        sleep 1
        if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
            printf "\n  \033[0;32m✅ Ready → http://localhost:%s\033[0m\n\n" "$PORT"
            if command -v open &>/dev/null; then
                open "http://localhost:$PORT"
            elif command -v xdg-open &>/dev/null; then
                xdg-open "http://localhost:$PORT" &>/dev/null
            fi
            exit 0
        fi
        i=$((i + 1))
        if [ $i -eq 30 ]; then
            printf "\n  \033[1;33m  Still starting... (first run may take a minute)\033[0m\n\n"
        fi
    done
    printf "\n  \033[1;33m⚠  Server did not respond after 120s — check logs above\033[0m\n"
) &

# Run uvicorn in foreground — logs visible normally, Ctrl+C kills it cleanly
exec uvicorn main:app --host 0.0.0.0 --port $PORT --reload \
    --reload-exclude 'venv/**' \
    --reload-exclude '*.pyc' \
    --reload-exclude '__pycache__/**'
