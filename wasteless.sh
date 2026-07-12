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

# Robustesse PATH. La boucle d'auto-collecte (_ensure_cron) reexecute ce script
# toutes les 5 min en heritant du PATH du shell qui a lance `wasteless start`.
# Lance depuis un contexte GUI / IDE / launchd, ce PATH n'inclut pas Homebrew,
# et `command -v steampipe` echoue alors que le binaire est installe -> les
# detecteurs 7-10 (elb/nat/vpc/gp2) sont faussement "skipped" et le dashboard
# affiche une collecte partielle. On prepend les emplacements standards pour
# que la detection ne depende pas du shell appelant. Prepend inoffensif si
# deja present. Comme la boucle re-source ce script a chaque tick, le prochain
# cycle s'auto-repare sans redemarrage.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

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

# Auto-collection scheduling (voir _schedule). Un seul intervalle, reutilise par
# les trois backends (launchd / systemd / cron).
COLLECT_INTERVAL_SEC=300
LAUNCHD_LABEL="io.wasteless.collect"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SYSTEMD_UNIT="wasteless-collect"
CRON_MARKER="# wasteless-collect"

CMD="${1:-start}"

# ---------------------------------------------------------------------------
# Detection de plateforme pour le scheduler d'auto-collecte
# ---------------------------------------------------------------------------
_is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

_has_systemd() {
    [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ] \
        && command -v systemctl >/dev/null 2>&1
}

# Choisit le meilleur backend disponible et le place dans $PLATFORM :
#   macos   -> launchd LaunchAgent
#   systemd -> systemd user timer (Linux natif ou WSL2 avec systemd=true)
#   cron    -> crontab (Linux/WSL sans systemd)
_detect_platform() {
    if [ "$(uname -s)" = "Darwin" ]; then
        PLATFORM="macos"
    elif _has_systemd; then
        PLATFORM="systemd"
    else
        PLATFORM="cron"
    fi
}

# Un scheduler OS est-il deja pose ? (evite que le loop bash fasse doublon)
_scheduler_installed() {
    [ -f "$LAUNCHD_PLIST" ] \
        || [ -f "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.timer" ] \
        || crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"
}

# Ouvre l'URL dans le navigateur par defaut, sans bruit terminal.
_open_browser() {
    if command -v open &>/dev/null; then
        open "$1"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "$1" &>/dev/null
    fi
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
_start() {
    cd "$SCRIPT_DIR/ui" || exit 1

    if [ ! -f .env ]; then
        echo -e "${RED}[ERROR]${NC} ui/.env not found. Run ./install.sh first."
        exit 1
    fi
    # Pas de `source .env` : l'app charge elle-même ui/.env (load_dotenv dans
    # ui/state.py), le shell n'a besoin que du port et du host. Sourcer le
    # fichier exécuterait du shell si un mot de passe contient $, ` ou espace
    # (même raison que get_env_var dans install.sh).
    _env_var() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2-; }

    if [ -d "venv" ]; then
        source venv/bin/activate
    elif [ -d ".venv" ]; then
        source .venv/bin/activate
    else
        echo -e "${RED}[ERROR]${NC} Virtual environment not found in ui/. Run ./install.sh first."
        exit 1
    fi

    PORT="${WASTELESS_PORT:-$(_env_var STREAMLIT_SERVER_PORT)}"
    PORT="${PORT:-8888}"

    # Page d'atterrissage : le wizard /setup tant qu'aucune connexion AWS
    # n'est configuree (ARNs/cles dans ui/.env — ecrits par install.sh et
    # /setup —, variables d'environnement, ou credentials partages crees par
    # `aws configure`). Dans le doute on ouvre la home : mieux vaut manquer
    # /setup que d'y renvoyer un compte deja connecte.
    LANDING_URL="http://localhost:$PORT"
    if [ -z "$(_env_var AWS_ROLE_ARN)$(_env_var AWS_ACCESS_KEY_ID)" ] \
        && [ -z "${AWS_ROLE_ARN:-}${AWS_ACCESS_KEY_ID:-}" ] \
        && [ ! -f "$HOME/.aws/credentials" ]; then
        LANDING_URL="http://localhost:$PORT/setup"
    fi

    # Already running? On ouvre quand meme le navigateur : ce chemin est
    # atteint quand l'utilisateur relance `wasteless` (ou re-execute
    # install.sh) pendant que le serveur tourne — imprimer l'URL sans
    # l'ouvrir laissait l'utilisateur la recopier a la main.
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}WasteLess is already running → http://localhost:$PORT${NC}"
        _open_browser "$LANDING_URL"
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
    # Loopback by default: the API has no authentication and its POST
    # endpoints execute real AWS actions. To expose it on the network,
    # set WASTELESS_HOST=0.0.0.0 explicitly (at your own risk).
    # Env var wins over ui/.env, which wins over the 127.0.0.1 default.
    HOST="${WASTELESS_HOST:-$(_env_var WASTELESS_HOST)}"
    HOST="${HOST:-127.0.0.1}"
    nohup uvicorn main:app --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
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
            if [ "$LANDING_URL" != "http://localhost:$PORT" ]; then
                echo -e "  ${YELLOW}AWS not connected yet — opening the setup guide (/setup)${NC}"
                echo ""
            fi
            _open_browser "$LANDING_URL"
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
                _open_browser "$LANDING_URL"
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

    cd "$SCRIPT_DIR" || exit 1

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
        if [ "$(uname)" = "Darwin" ]; then
            echo "  Install with: brew install turbot/tap/steampipe && steampipe plugin install aws"
        else
            echo "  Install with: sudo /bin/sh -c \"\$(curl -fsSL https://steampipe.io/install/steampipe.sh)\" && steampipe plugin install aws"
        fi
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
    # Un scheduler OS (launchd/systemd/cron) prend le relais et survit au reboot
    # comme a l'arret de l'UI : dans ce cas le loop bash ne sert a rien et ferait
    # doublon. On lui laisse la main.
    if _scheduler_installed; then
        echo -e "  ${CYAN}Auto-collection: scheduler OS actif (survit au reboot)${NC}"
        return
    fi

    # Fallback : ni launchd ni systemd ni cron installes (ex: 'wasteless start'
    # sans avoir lance 'wasteless schedule'). Loop en process, lie a cette
    # session -- ne survit ni au reboot ni a 'wasteless stop'.
    if [ -f "$COLLECTOR_PID_FILE" ] && kill -0 "$(cat "$COLLECTOR_PID_FILE")" 2>/dev/null; then
        return
    fi

    (
        SELF="$SCRIPT_DIR/wasteless.sh"
        while true; do
            "$SELF" collect >> "$LOG_FILE" 2>&1
            sleep "$COLLECT_INTERVAL_SEC"
        done
    ) &
    echo $! > "$COLLECTOR_PID_FILE"
    disown $!
    echo -e "  ${CYAN}Auto-collection started (in-process, every 5 min)${NC}"
    echo -e "  ${YELLOW}Tip:${NC} 'wasteless schedule' pour une collecte qui survit au reboot"
}

# ---------------------------------------------------------------------------
# schedule / unschedule  — collecte automatique au niveau OS (survit au reboot)
# ---------------------------------------------------------------------------
_schedule_macos() {
    mkdir -p "$(dirname "$LAUNCHD_PLIST")"
    cat > "$LAUNCHD_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${SCRIPT_DIR}/wasteless.sh</string>
        <string>collect</string>
    </array>
    <key>StartInterval</key><integer>${COLLECT_INTERVAL_SEC}</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>${LOG_FILE}</string>
    <key>StandardErrorPath</key><string>${LOG_FILE}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST
    launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
    launchctl load -w "$LAUNCHD_PLIST"
    echo -e "  ${GREEN}[OK]${NC} LaunchAgent installe: $LAUNCHD_PLIST (toutes les 5 min, RunAtLoad)"
}

_schedule_systemd() {
    mkdir -p "$SYSTEMD_USER_DIR"
    cat > "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.service" <<UNIT
[Unit]
Description=WasteLess AWS collection & waste detection
After=network-online.target

[Service]
Type=oneshot
ExecStart=${SCRIPT_DIR}/wasteless.sh collect
UNIT
    cat > "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.timer" <<UNIT
[Unit]
Description=Run WasteLess collection every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=${COLLECT_INTERVAL_SEC}
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now "${SYSTEMD_UNIT}.timer"
    # enable-linger : le user manager demarre au boot sans login interactif
    # (indispensable sur un VPS headless accede en SSH ponctuel).
    if ! loginctl enable-linger "$USER" 2>/dev/null; then
        sudo loginctl enable-linger "$USER" 2>/dev/null \
            || echo -e "  ${YELLOW}[WARN]${NC} enable-linger a echoue — la collecte peut s'arreter a la deconnexion. Lancez: sudo loginctl enable-linger $USER"
    fi
    echo -e "  ${GREEN}[OK]${NC} systemd user timer actif: ${SYSTEMD_UNIT}.timer (toutes les 5 min, persistant)"
}

_schedule_cron() {
    local self="$SCRIPT_DIR/wasteless.sh"
    local entry="*/5 * * * * $self collect >> $LOG_FILE 2>&1 $CRON_MARKER"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_MARKER"; echo "$entry" ) | crontab -
    echo -e "  ${GREEN}[OK]${NC} entree crontab ajoutee (toutes les 5 min)"
    if _is_wsl && ! _has_systemd; then
        echo -e "  ${YELLOW}[WARN]${NC} WSL sans systemd : cron ne tourne que si la distro est active."
        echo "  Pour une collecte fiable, activez systemd dans /etc/wsl.conf :"
        echo "    [boot]"
        echo "    systemd=true"
        echo "  (puis 'wsl --shutdown' cote Windows et relancez 'wasteless schedule')"
        echo "  Alternative cote Windows (Task Scheduler, toutes les 5 min) :"
        echo "    schtasks /Create /SC MINUTE /MO 5 /TN WasteLessCollect \\"
        echo "      /TR \"wsl.exe -e $self collect\""
    fi
}

_schedule() {
    _detect_platform
    echo -e "${BOLD}Installation de la collecte automatique (${PLATFORM})${NC}"
    case "$PLATFORM" in
        macos)   _schedule_macos ;;
        systemd) _schedule_systemd ;;
        cron)    _schedule_cron ;;
    esac
    # Le loop bash en process n'a plus lieu d'etre : on le coupe s'il tournait.
    if [ -f "$COLLECTOR_PID_FILE" ]; then
        kill "$(cat "$COLLECTOR_PID_FILE")" 2>/dev/null || true
        rm -f "$COLLECTOR_PID_FILE"
    fi
    echo -e "  Desactivation : ${CYAN}wasteless unschedule${NC}"
}

_unschedule() {
    _detect_platform
    case "$PLATFORM" in
        macos)
            launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
            rm -f "$LAUNCHD_PLIST"
            echo -e "  ${GREEN}[OK]${NC} LaunchAgent supprime"
            ;;
        systemd)
            systemctl --user disable --now "${SYSTEMD_UNIT}.timer" 2>/dev/null || true
            rm -f "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.timer" "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.service"
            systemctl --user daemon-reload 2>/dev/null || true
            echo -e "  ${GREEN}[OK]${NC} systemd timer supprime"
            ;;
        cron)
            crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" | crontab - 2>/dev/null || true
            echo -e "  ${GREEN}[OK]${NC} entree crontab supprimee"
            ;;
    esac
}

_schedule_status() {
    _detect_platform
    echo -e "${BOLD}Auto-collection (${PLATFORM})${NC}"
    case "$PLATFORM" in
        macos)
            if [ -f "$LAUNCHD_PLIST" ]; then
                echo -e "  ${GREEN}Active${NC} — $LAUNCHD_PLIST"
                launchctl list 2>/dev/null | grep -F "$LAUNCHD_LABEL" || true
            else
                echo "  Inactive (lancez 'wasteless schedule')"
            fi
            ;;
        systemd)
            if [ -f "$SYSTEMD_USER_DIR/${SYSTEMD_UNIT}.timer" ]; then
                systemctl --user list-timers "${SYSTEMD_UNIT}.timer" --no-pager 2>/dev/null || true
            else
                echo "  Inactive (lancez 'wasteless schedule')"
            fi
            ;;
        cron)
            if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
                echo -e "  ${GREEN}Active${NC} —"
                crontab -l 2>/dev/null | grep -F "$CRON_MARKER"
            else
                echo "  Inactive (lancez 'wasteless schedule')"
            fi
            ;;
    esac
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
case "$CMD" in
    "" | start)   _start   ;;
    stop)         _stop    ;;
    logs)         _logs    ;;
    status)       _status; echo ""; _schedule_status ;;
    collect)      _collect ;;
    schedule)     _schedule ;;
    unschedule)   _unschedule ;;
    *)
        echo -e "${BOLD}Usage:${NC} wasteless [command]"
        echo ""
        echo "Commands:"
        echo "  start        Start the web UI in background (default)"
        echo "  stop         Stop the web UI"
        echo "  logs         View server logs (tail -f)"
        echo "  status       Check if server + auto-collection are running"
        echo "  collect      Collect AWS metrics and detect idle instances (once)"
        echo "  schedule     Install OS-level auto-collection every 5 min (survives reboot)"
        echo "  unschedule   Remove the OS-level auto-collection"
        echo ""
        exit 1
        ;;
esac
