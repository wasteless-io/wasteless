#!/bin/bash
#
# WasteLess UI - Script d'installation automatique
#
# Usage: ./install.sh
#
# Ce script configure automatiquement l'interface WasteLess:
# - Verifie les prerequis (Python, Backend)
# - Cree l'environnement virtuel Python
# - Installe les dependances
# - Configure le fichier .env
# - Verifie l'integration avec le backend
# - Cree l'alias 'wasteless' pour demarrer l'application
#

set -e  # Exit on error

# =============================================================================
# COULEURS ET FORMATAGE
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================
print_header() {
    echo ""
    echo -e "${BLUE}=======================================================================${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BLUE}=======================================================================${NC}"
    echo ""
}

print_step() {
    echo -e "${BOLD}${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${BOLD}${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${BOLD}${RED}[ERROR]${NC} $1"
}

print_info() {
    echo -e "${BOLD}${BLUE}[INFO]${NC} $1"
}

check_command() {
    if command -v "$1" &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# =============================================================================
# BANNIERE
# =============================================================================
clear
echo -e "${CYAN}"
cat << "EOF"
 __        __        _       _                   _   _ ___
 \ \      / /_ _ ___| |_ ___| | ___  ___ ___    | | | |_ _|
  \ \ /\ / / _` / __| __/ _ \ |/ _ \/ __/ __|   | | | || |
   \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \   | |_| || |
    \_/\_/ \__,_|___/\__\___|_|\___||___/___/    \___/|___|

    FastAPI Dashboard for Cloud Cost Optimization
EOF
echo -e "${NC}"
echo -e "${BOLD}Version 1.0 - Installation automatique${NC}"
echo ""

# =============================================================================
# DETECTION DU REPERTOIRE
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detecter le chemin du backend (dossier parent de ui/)
BACKEND_PATH="$(dirname "$SCRIPT_DIR")"

# =============================================================================
# VERIFICATION DES PREREQUIS
# =============================================================================
print_header "1/6 - Verification des prerequis"

MISSING_DEPS=0

# Python 3.10+
if check_command python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        print_step "Python $PYTHON_VERSION detecte"
    else
        print_error "Python 3.10+ requis (trouve: $PYTHON_VERSION)"
        MISSING_DEPS=1
    fi
else
    print_error "Python3 non trouve"
    MISSING_DEPS=1
fi

# Backend WasteLess
if [ -d "$BACKEND_PATH" ]; then
    print_step "Backend WasteLess detecte: $BACKEND_PATH"
else
    print_error "Backend WasteLess non trouve (ce script doit etre execute depuis ui/ dans le repo wasteless)"
    print_info "Utilisez install.sh a la racine du projet: ./install.sh"
    MISSING_DEPS=1
fi

# PostgreSQL (via Docker ou direct)
if check_command docker; then
    if docker ps | grep -q wasteless-postgres 2>/dev/null; then
        print_step "PostgreSQL detecte (Docker)"
    elif check_command psql; then
        print_step "PostgreSQL detecte (local)"
    else
        print_warning "PostgreSQL non demarre"
        print_info "Demarrez PostgreSQL: cd ../wasteless && docker compose up -d postgres"
    fi
else
    if check_command psql; then
        print_step "PostgreSQL detecte (local)"
    else
        print_warning "PostgreSQL non detecte"
        print_info "Installez Docker ou PostgreSQL local"
    fi
fi

# Git (optionnel)
if check_command git; then
    print_step "Git detecte"
else
    print_warning "Git non trouve (optionnel)"
fi

# Verifier si des dependances manquent
if [ $MISSING_DEPS -eq 1 ]; then
    echo ""
    print_error "Certains prerequis sont manquants. Installez-les et reexecutez ce script."
    exit 1
fi

echo ""
print_step "Tous les prerequis sont satisfaits"

# =============================================================================
# CREATION DE L'ENVIRONNEMENT VIRTUEL
# =============================================================================
print_header "2/6 - Configuration de l'environnement Python"

if [ -d "venv" ]; then
    print_warning "Environnement virtuel existant detecte"
    read -p "Voulez-vous le recreer? (o/N): " RECREATE_VENV
    if [[ "$RECREATE_VENV" =~ ^[Oo]$ ]]; then
        rm -rf venv
        python3 -m venv venv
        print_step "Environnement virtuel recree"
    else
        print_step "Environnement virtuel conserve"
    fi
else
    python3 -m venv venv
    print_step "Environnement virtuel cree"
fi

# Activation et installation des dependances
source venv/bin/activate
print_step "Environnement virtuel active"

pip install --upgrade pip -q
pip install -r requirements.txt -q
print_step "Dependances Python installees"

# =============================================================================
# CONFIGURATION DU FICHIER .ENV
# =============================================================================
print_header "3/6 - Configuration de l'application"

if [ -f ".env" ]; then
    print_warning "Fichier .env existant detecte"
    read -p "Voulez-vous le reconfigurer? (o/N): " RECONFIG_ENV
    if [[ ! "$RECONFIG_ENV" =~ ^[Oo]$ ]]; then
        print_step "Configuration existante conservee"
        SKIP_ENV_CONFIG=1
    fi
fi

if [ -z "$SKIP_ENV_CONFIG" ]; then
    echo ""
    print_info "Configuration de la base de donnees"
    echo ""

    # Essayer de recuperer le mot de passe du backend
    BACKEND_ENV="$BACKEND_PATH/.env"
    if [ -f "$BACKEND_ENV" ]; then
        print_info "Configuration backend detectee"
        source "$BACKEND_ENV" 2>/dev/null || true

        if [ -n "$DB_PASSWORD" ]; then
            print_step "Mot de passe recupere depuis le backend"
        fi
    fi

    # Demander le mot de passe si pas recupere
    if [ -z "$DB_PASSWORD" ]; then
        while true; do
            read -sp "Mot de passe de la base de donnees: " DB_PASSWORD
            echo ""
            if [ ${#DB_PASSWORD} -lt 8 ]; then
                print_error "Le mot de passe doit contenir au moins 8 caracteres"
            else
                break
            fi
        done
    fi

    # Configuration DB
    read -p "Host PostgreSQL [localhost]: " DB_HOST
    DB_HOST=${DB_HOST:-localhost}

    read -p "Port PostgreSQL [5432]: " DB_PORT
    DB_PORT=${DB_PORT:-5432}

    read -p "Nom de la base [wasteless]: " DB_NAME
    DB_NAME=${DB_NAME:-wasteless}

    read -p "Utilisateur PostgreSQL [wasteless]: " DB_USER
    DB_USER=${DB_USER:-wasteless}

    echo ""
    print_info "Configuration du backend"
    echo ""

    read -p "Chemin du backend [$BACKEND_PATH]: " BACKEND_INPUT
    BACKEND_PATH=${BACKEND_INPUT:-$BACKEND_PATH}

    # Port application
    read -p "Port application [8888]: " APP_PORT
    APP_PORT=${APP_PORT:-8888}

    # Creation du fichier .env
    cat > .env << EOF
# ===========================================
# WasteLess UI Configuration
# Generated: $(date)
# ===========================================

# Database Connection
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD

# Backend Integration
WASTELESS_BACKEND_PATH=$BACKEND_PATH

# UI Configuration
STREAMLIT_SERVER_PORT=$APP_PORT
STREAMLIT_SERVER_ADDRESS=localhost

# Log Level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL=INFO

# Performance Settings (optional)
# MAX_RECOMMENDATIONS_TOTAL=500
# MAX_RECOMMENDATIONS_PER_PAGE=100
# MAX_HISTORY_RECORDS=100
EOF

    chmod 600 .env
    print_step "Fichier .env cree et securise"
fi

# =============================================================================
# VERIFICATION DE L'INTEGRATION
# =============================================================================
print_header "4/6 - Verification de l'integration"

source .env

# Test de connexion DB
print_info "Test de connexion a la base de donnees..."
if python3 -c "
import psycopg2
import os
try:
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        database=os.getenv('DB_NAME', 'wasteless'),
        user=os.getenv('DB_USER', 'wasteless'),
        password=os.getenv('DB_PASSWORD')
    )
    conn.close()
    exit(0)
except Exception as e:
    print(f'Erreur: {e}')
    exit(1)
" 2>/dev/null; then
    print_step "Connexion base de donnees OK"
else
    print_warning "Connexion base de donnees echouee"
    print_info "Verifiez que PostgreSQL est demarre et que les credentials sont corrects"
fi

# Test du backend
print_info "Verification du backend..."
if [ -f "$BACKEND_PATH/src/remediators/ec2_remediator.py" ]; then
    print_step "Module EC2Remediator trouve"
else
    print_warning "Module EC2Remediator non trouve"
    print_info "Verifiez le chemin du backend: $BACKEND_PATH"
fi

# Test des imports Python
print_info "Verification des modules Python..."
if python3 -c "
import fastapi
import uvicorn
import jinja2
import psycopg2
import yaml
print('OK')
" 2>/dev/null; then
    print_step "Modules Python OK"
else
    print_error "Certains modules Python sont manquants"
    print_info "Executez: pip install -r requirements.txt"
fi

# =============================================================================
# EXECUTION DES TESTS
# =============================================================================
print_header "5/6 - Execution des tests"

print_info "Lancement des tests unitaires..."
if python3 run_tests.py 2>/dev/null; then
    print_step "Tests unitaires OK"
else
    print_warning "Certains tests ont echoue ou ont ete ignores"
    print_info "Executez 'python run_tests.py' pour plus de details"
fi

# =============================================================================
# CREATION DE L'ALIAS
# =============================================================================
print_header "6/6 - Configuration de l'alias 'wasteless'"

SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

# Alias pointe vers wasteless.sh (CLI racine), pas ui/start.sh
WASTELESS_CLI="$(dirname "$SCRIPT_DIR")/wasteless.sh"
ALIAS_LINE="alias wasteless='$WASTELESS_CLI'"

if [ -n "$SHELL_RC" ]; then
    if grep -q "alias wasteless='$WASTELESS_CLI'" "$SHELL_RC" 2>/dev/null; then
        print_step "Alias 'wasteless' deja present dans $SHELL_RC"
    elif grep -q "alias wasteless=" "$SHELL_RC" 2>/dev/null; then
        sed -i '' "s|alias wasteless=.*|$ALIAS_LINE|" "$SHELL_RC"
        print_step "Alias 'wasteless' mis a jour dans $SHELL_RC"
    else
        echo "" >> "$SHELL_RC"
        echo "# WasteLess CLI" >> "$SHELL_RC"
        echo "$ALIAS_LINE" >> "$SHELL_RC"
        print_step "Alias 'wasteless' ajoute a $SHELL_RC"
    fi
else
    print_warning "Shell non detecte. Ajoutez manuellement:"
    echo "  alias wasteless='$WASTELESS_CLI'"
fi

# =============================================================================
# RESUME ET PROCHAINES ETAPES
# =============================================================================
print_header "Installation terminee"

# Get port from .env
source .env
APP_PORT="${STREAMLIT_SERVER_PORT:-8888}"

echo -e "${GREEN}${BOLD}WasteLess UI a ete installe avec succes!${NC}"
echo ""
echo -e "${BOLD}Configuration:${NC}"
echo "  - Base de donnees: $DB_HOST:$DB_PORT/$DB_NAME"
echo "  - Backend: $BACKEND_PATH"
echo "  - Port UI: $APP_PORT"
echo ""
echo -e "${BOLD}Prochaines etapes:${NC}"
echo ""
echo -e "  1. ${CYAN}Rechargez votre shell:${NC}"
echo "     source $SHELL_RC"
echo ""
echo -e "  2. ${CYAN}Demarrez l'interface:${NC}"
echo "     wasteless"
echo "     -> Acces: http://localhost:$APP_PORT"
echo ""
echo -e "  3. ${CYAN}Ou avec le script:${NC}"
echo "     ./start.sh"
echo ""
echo -e "${BOLD}Pages disponibles:${NC}"
echo "  - Home:            Vue d'ensemble"
echo "  - Dashboard:       Metriques et graphiques"
echo "  - Recommendations: Approuver/Rejeter les actions"
echo "  - History:         Historique des remediations"
echo "  - Settings:        Configuration et whitelist"
echo ""
echo -e "${BOLD}Commandes utiles:${NC}"
echo "  - Demarrer:      wasteless"
echo "  - Lancer tests:  python run_tests.py"
echo "  - Voir logs:     tail -f logs/wasteless_ui.log"
echo ""
echo -e "${YELLOW}${BOLD}Important:${NC}"
echo "  Le mode DRY-RUN est actif par defaut."
echo "  Les actions n'affecteront pas vos instances AWS."
echo "  Configurez dry_run_days dans Settings avant production."
echo ""
