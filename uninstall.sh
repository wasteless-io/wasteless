#!/bin/bash
#
# WasteLess - Script de desinstallation
#
# Usage:
#   ./uninstall.sh          # Desinstallation standard (conserve les donnees DB)
#   ./uninstall.sh --full   # Desinstallation complete (supprime aussi les donnees DB)
#

# =============================================================================
# COULEURS ET FORMATAGE
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}=======================================================================${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BLUE}=======================================================================${NC}"
    echo ""
}

print_step()    { echo -e "${BOLD}${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${BOLD}${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${BOLD}${RED}[ERROR]${NC} $1"; }
print_info()    { echo -e "${BOLD}${BLUE}[INFO]${NC} $1"; }
print_skip()    { echo -e "${BOLD}${CYAN}[SKIP]${NC} $1"; }

# =============================================================================
# OPTIONS
# =============================================================================
FULL_UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL_UNINSTALL=1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =============================================================================
# BANNIERE
# =============================================================================
clear
echo -e "${RED}"
cat << "EOF"
 __        __        _       _
 \ \      / /_ _ ___| |_ ___| | ___  ___ ___
  \ \ /\ / / _` / __| __/ _ \ |/ _ \/ __/ __|
   \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \
    \_/\_/ \__,_|___/\__\___|_|\___||___/___/

    Desinstallation
EOF
echo -e "${NC}"

if [ "$FULL_UNINSTALL" -eq 1 ]; then
    echo -e "${RED}${BOLD}Mode: DESINSTALLATION COMPLETE (donnees supprimees)${NC}"
else
    echo -e "${YELLOW}${BOLD}Mode: Desinstallation standard (donnees conservees)${NC}"
    echo -e "  Utilisez ${BOLD}--full${NC} pour supprimer egalement les donnees."
fi
echo ""
echo -e "${YELLOW}${BOLD}Appuyez sur Entree pour continuer ou Ctrl+C pour annuler...${NC}"
read -r

# =============================================================================
# 1. ARRETER LE PROCESSUS WASTELESS
# =============================================================================
print_header "1/5 - Arret de WasteLess"

PID_FILE="$HOME/.wasteless.pid"
COLLECTOR_PID_FILE="$HOME/.wasteless-collector.pid"

# Arreter le collector loop
if [ -f "$COLLECTOR_PID_FILE" ]; then
    CPID=$(cat "$COLLECTOR_PID_FILE")
    kill "$CPID" 2>/dev/null || true
    rm -f "$COLLECTOR_PID_FILE"
    print_step "Collector automatique arrete (PID $CPID)"
else
    print_skip "Aucun collector automatique en cours"
fi

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null
        fi
        print_step "Processus WasteLess ($PID) arrete"
    else
        print_skip "Processus $PID deja arrete"
    fi
    rm -f "$PID_FILE"
    print_step "Fichier PID supprime"
else
    print_skip "Aucun processus WasteLess en cours"
fi

# Supprimer le fichier de log daemon
LOG_FILE="$HOME/.wasteless.log"
if [ -f "$LOG_FILE" ]; then
    rm -f "$LOG_FILE"
    print_step "Fichier de log supprime ($LOG_FILE)"
else
    print_skip "Aucun fichier de log a supprimer"
fi

# =============================================================================
# 2. ARRETER ET SUPPRIMER LES CONTENEURS DOCKER
# =============================================================================
print_header "2/5 - Arret des services Docker"

if command -v docker &>/dev/null && [ -f "$SCRIPT_DIR/docker-compose.yml" ] || [ -f "$SCRIPT_DIR/compose.yml" ]; then
    if docker ps | grep -q wasteless 2>/dev/null; then
        docker compose down 2>/dev/null
        print_step "Conteneurs Docker arretes"
    else
        print_skip "Aucun conteneur WasteLess en cours d'execution"
    fi

    if [ "$FULL_UNINSTALL" -eq 1 ]; then
        echo ""
        print_warning "Suppression des volumes Docker (donnees de la base de donnees)..."
        docker compose down -v 2>/dev/null || true
        docker volume rm wasteless_postgres_data 2>/dev/null || true
        print_step "Volumes Docker supprimes (donnees effacees)"
    else
        print_skip "Volumes Docker conserves (utilisez --full pour les supprimer)"
    fi
else
    print_skip "Docker non disponible ou docker-compose.yml absent"
fi

# =============================================================================
# 3. SUPPRIMER LES ENVIRONNEMENTS VIRTUELS ET FICHIERS DE CONFIG
# =============================================================================
print_header "3/5 - Suppression des fichiers locaux"

# Environnement virtuel backend
if [ -d "$SCRIPT_DIR/venv" ]; then
    rm -rf "$SCRIPT_DIR/venv"
    print_step "Environnement virtuel backend supprime (venv/)"
else
    print_skip "Environnement virtuel backend absent"
fi

# Environnement virtuel UI
if [ -d "$SCRIPT_DIR/ui/venv" ]; then
    rm -rf "$SCRIPT_DIR/ui/venv"
    print_step "Environnement virtuel UI supprime (ui/venv/)"
else
    print_skip "Environnement virtuel UI absent"
fi

# Fichiers .env
for ENV_FILE in "$SCRIPT_DIR/.env" "$SCRIPT_DIR/ui/.env"; do
    if [ -f "$ENV_FILE" ]; then
        rm -f "$ENV_FILE"
        print_step "Fichier de configuration supprime: $ENV_FILE"
    else
        print_skip "Absent: $ENV_FILE"
    fi
done

# Caches Python
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$SCRIPT_DIR" -name "*.pyc" -delete 2>/dev/null || true
print_step "Caches Python supprimes"

# =============================================================================
# 4. SUPPRIMER LES CRON JOBS
# =============================================================================
print_header "4/5 - Suppression des taches automatiques (cron)"

CRON_MARKERS=(
    "# Wasteless: Automated CloudWatch metrics collection"
    "# Wasteless: Automated waste detection"
    "# Wasteless: Automated cleanup of orphaned recommendations"
    "# wasteless-collect"
)

CRONTAB_CONTENT=$(crontab -l 2>/dev/null || true)

if [ -z "$CRONTAB_CONTENT" ]; then
    print_skip "Aucun crontab configure"
else
    HAS_WASTELESS=0
    for marker in "${CRON_MARKERS[@]}"; do
        if echo "$CRONTAB_CONTENT" | grep -qF "$marker"; then
            HAS_WASTELESS=1
            break
        fi
    done

    if [ "$HAS_WASTELESS" -eq 1 ]; then
        # Supprimer les blocs wasteless du crontab
        CLEANED=$(echo "$CRONTAB_CONTENT" | awk '
            /# Wasteless:/ { skip=2; next }
            skip > 0 { skip--; next }
            { print }
        ')
        echo "$CLEANED" | crontab -
        print_step "Taches cron WasteLess supprimees"
    else
        print_skip "Aucune tache cron WasteLess trouvee"
    fi
fi

# =============================================================================
# 5. SUPPRIMER L'ALIAS DU SHELL
# =============================================================================
print_header "5/5 - Suppression de l'alias 'wasteless'"

SHELL_RCS=("$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc")
ALIAS_REMOVED=0

for SHELL_RC in "${SHELL_RCS[@]}"; do
    if [ -f "$SHELL_RC" ] && grep -q "alias wasteless=" "$SHELL_RC" 2>/dev/null; then
        # Supprimer la ligne de commentaire et l'alias
        sed -i '' '/# WasteLess CLI/d' "$SHELL_RC"
        sed -i '' '/alias wasteless=/d' "$SHELL_RC"
        print_step "Alias supprime de $SHELL_RC"
        ALIAS_REMOVED=1
    fi
done

if [ "$ALIAS_REMOVED" -eq 0 ]; then
    print_skip "Aucun alias 'wasteless' trouve dans les fichiers shell"
fi

# =============================================================================
# RESUME
# =============================================================================
print_header "Desinstallation terminee"

echo -e "${GREEN}${BOLD}WasteLess a ete desinstalle.${NC}"
echo ""
echo -e "${BOLD}Ce qui a ete supprime:${NC}"
echo "  - Processus WasteLess (si en cours)"
echo "  - Conteneurs Docker"
echo "  - Environnements virtuels Python (venv/, ui/venv/)"
echo "  - Fichiers de configuration (.env, ui/.env)"
echo "  - Caches Python (__pycache__/, *.pyc)"
echo "  - Taches cron automatiques"
echo "  - Alias 'wasteless' du shell"
echo ""

if [ "$FULL_UNINSTALL" -eq 1 ]; then
    echo -e "${RED}${BOLD}Donnees supprimees:${NC}"
    echo "  - Volumes Docker (base de donnees PostgreSQL)"
    echo ""
else
    echo -e "${YELLOW}${BOLD}Conserve:${NC}"
    echo "  - Volumes Docker (base de donnees PostgreSQL)"
    echo "  - Code source du projet"
    echo ""
    echo -e "  Pour supprimer aussi les donnees: ${BOLD}./uninstall.sh --full${NC}"
fi

echo -e "${BOLD}Pour recharger votre shell:${NC}"
for SHELL_RC in "${SHELL_RCS[@]}"; do
    if [ -f "$SHELL_RC" ]; then
        echo "  source $SHELL_RC"
        break
    fi
done
echo ""
