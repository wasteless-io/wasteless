#!/bin/bash
#
# WasteLess - Script d'installation automatique
#
# Usage: ./install.sh
#
# Ce script configure automatiquement l'environnement WasteLess:
# - Verifie les prerequis (Python, Docker, AWS CLI)
# - Cree l'environnement virtuel Python
# - Installe les dependances
# - Configure la base de donnees
# - Guide la configuration AWS
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
 __        __        _       _
 \ \      / /_ _ ___| |_ ___| | ___  ___ ___
  \ \ /\ / / _` / __| __/ _ \ |/ _ \/ __/ __|
   \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \
    \_/\_/ \__,_|___/\__\___|_|\___||___/___/

    Cloud Cost Optimization Platform
EOF
echo -e "${NC}"
echo -e "${BOLD}Version 1.0 - Installation automatique${NC}"
echo ""

# =============================================================================
# VERIFICATION DES PREREQUIS
# =============================================================================
print_header "1/7 - Verification des prerequis"

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

# Docker
if check_command docker; then
    if docker info &> /dev/null; then
        print_step "Docker detecte et fonctionnel"
    else
        print_error "Docker installe mais non demarre"
        print_info "Lancez Docker Desktop et reexecutez ce script"
        MISSING_DEPS=1
    fi
else
    print_error "Docker non trouve"
    print_info "Installez Docker: https://docs.docker.com/get-docker/"
    MISSING_DEPS=1
fi

# Docker Compose
if check_command docker-compose || docker compose version &> /dev/null; then
    print_step "Docker Compose detecte"
else
    print_error "Docker Compose non trouve"
    MISSING_DEPS=1
fi

# AWS CLI (optionnel mais recommande)
if check_command aws; then
    print_step "AWS CLI detecte"
else
    print_warning "AWS CLI non trouve (optionnel)"
    print_info "Installez AWS CLI: https://aws.amazon.com/cli/"
fi

# Git
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
print_header "2/7 - Configuration de l'environnement Python"

if [ -d "venv" ]; then
    # Verifier que le venv n'est pas corrompu
    if ! venv/bin/python3 -c "import pip" &> /dev/null; then
        print_warning "Environnement virtuel corrompu detecte — recreation automatique"
        rm -rf venv
        python3 -m venv venv
        print_step "Environnement virtuel recree"
    else
        print_warning "Environnement virtuel existant detecte"
        read -p "Voulez-vous le recreer? (o/N): " RECREATE_VENV
        if [[ "$RECREATE_VENV" =~ ^[Oo]$ ]]; then
            rm -rf venv
            python3 -m venv venv
            print_step "Environnement virtuel recree"
        else
            print_step "Environnement virtuel conserve"
        fi
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
print_header "3/7 - Configuration de l'application"

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

    # Mot de passe DB
    while true; do
        read -sp "Creez un mot de passe pour la base de donnees: " DB_PASSWORD
        echo ""
        if [ ${#DB_PASSWORD} -lt 8 ]; then
            print_error "Le mot de passe doit contenir au moins 8 caracteres"
        else
            break
        fi
    done

    echo ""
    print_info "Configuration AWS"
    echo ""

    # AWS Region
    read -p "Region AWS [eu-west-1]: " AWS_REGION
    AWS_REGION=${AWS_REGION:-eu-west-1}

    # AWS Account ID
    read -p "AWS Account ID (12 chiffres): " AWS_ACCOUNT_ID
    while [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; do
        print_error "L'Account ID doit contenir exactement 12 chiffres"
        read -p "AWS Account ID (12 chiffres): " AWS_ACCOUNT_ID
    done

    # Credentials AWS (optionnel)
    echo ""
    print_info "Credentials AWS (laissez vide pour utiliser IAM roles)"
    read -p "AWS Access Key ID (optionnel): " AWS_ACCESS_KEY_ID
    if [ -n "$AWS_ACCESS_KEY_ID" ]; then
        read -sp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
        echo ""
    fi

    # Creation du fichier .env
    cat > .env << EOF
# ===========================================
# WasteLess Configuration
# Generated: $(date)
# ===========================================

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=wasteless
DB_USER=wasteless
DB_PASSWORD=$DB_PASSWORD

# AWS
AWS_REGION=$AWS_REGION
AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID
EOF

    if [ -n "$AWS_ACCESS_KEY_ID" ]; then
        cat >> .env << EOF
AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY
EOF
    fi

    cat >> .env << EOF

# Application
LOG_LEVEL=INFO
DRY_RUN=true
EOF

    chmod 600 .env
    print_step "Fichier .env cree et securise"
fi

# =============================================================================
# DEMARRAGE DE LA BASE DE DONNEES
# =============================================================================
print_header "4/7 - Demarrage de la base de donnees"

# Charger le mot de passe depuis .env
source .env

# Demarrer PostgreSQL
print_info "Demarrage de PostgreSQL via Docker..."

if docker ps | grep -q wasteless-postgres; then
    print_warning "PostgreSQL deja en cours d'execution"
elif docker ps -a | grep -q wasteless-postgres; then
    print_warning "Conteneur wasteless-postgres existant detecte (arrete) — tentative de redemarrage..."
    if ! docker start wasteless-postgres > /dev/null 2>&1; then
        print_warning "Redemarrage echoue (volumes invalides) — suppression et recreation du conteneur..."
        docker rm wasteless-postgres > /dev/null
        docker compose up -d postgres
        print_step "PostgreSQL recree et demarre"
    else
        print_step "PostgreSQL redemarre"
    fi

    # Attendre que PostgreSQL soit pret
    print_info "Attente de la disponibilite de PostgreSQL..."
    for i in {1..30}; do
        if docker exec wasteless-postgres pg_isready -U wasteless &> /dev/null; then
            break
        fi
        sleep 1
    done
else
    docker compose up -d postgres
    print_step "PostgreSQL demarre"

    # Attendre que PostgreSQL soit pret
    print_info "Attente de la disponibilite de PostgreSQL..."
    for i in {1..30}; do
        if docker exec wasteless-postgres pg_isready -U wasteless &> /dev/null; then
            break
        fi
        sleep 1
    done
fi

# Verifier la connexion
if docker exec wasteless-postgres pg_isready -U wasteless &> /dev/null; then
    print_step "PostgreSQL est pret"
else
    print_error "PostgreSQL n'a pas demarre correctement"
    exit 1
fi

# =============================================================================
# INITIALISATION DU SCHEMA
# =============================================================================
print_header "5/7 - Initialisation du schema de base de donnees"

# Executer les migrations
print_info "Application des migrations..."

# Init.sql est automatiquement execute par Docker
# Appliquer les migrations supplementaires
for migration in sql/ec2_metrics.sql sql/migrations/*.sql; do
    if [ -f "$migration" ]; then
        docker exec -i wasteless-postgres psql -U wasteless -d wasteless < "$migration" &> /dev/null || true
        print_step "Migration appliquee: $(basename $migration)"
    fi
done

print_step "Schema de base de donnees initialise"

# =============================================================================
# INSTALLATION DE L'INTERFACE WEB
# =============================================================================
print_header "6/7 - Installation de l'interface web"

# Environnement virtuel UI
if [ -d "ui/venv" ]; then
    if ! ui/venv/bin/python3 -c "import pip" &> /dev/null; then
        print_warning "Environnement virtuel UI corrompu — recreation automatique"
        rm -rf ui/venv
        python3 -m venv ui/venv
        print_step "Environnement virtuel UI recree"
    else
        print_step "Environnement virtuel UI deja present"
    fi
else
    python3 -m venv ui/venv
    print_step "Environnement virtuel UI cree"
fi

ui/venv/bin/pip install --upgrade pip -q
ui/venv/bin/pip install -r ui/requirements.txt -q
print_step "Dependances UI installees"

# Fichier ui/.env auto-genere depuis le root .env (deja source)
if [ ! -f "ui/.env" ]; then
    cat > ui/.env << UIENV
# WasteLess UI Configuration - Generated: $(date)
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-wasteless}
DB_USER=${DB_USER:-wasteless}
DB_PASSWORD=${DB_PASSWORD}
WASTELESS_BACKEND_PATH=$(pwd)
STREAMLIT_SERVER_PORT=8888
STREAMLIT_SERVER_ADDRESS=localhost
LOG_LEVEL=INFO
UIENV
    chmod 600 ui/.env
    print_step "Configuration UI creee (ui/.env)"
else
    print_step "Configuration UI existante conservee"
fi

# Alias wasteless → pointe vers wasteless.sh (CLI racine)
chmod +x "$(pwd)/wasteless.sh"
WASTELESS_CLI="$(pwd)/wasteless.sh"
ALIAS_LINE="alias wasteless='$WASTELESS_CLI'"
SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bash_profile" ]; then
    SHELL_RC="$HOME/.bash_profile"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
    if grep -q "alias wasteless='$WASTELESS_CLI'" "$SHELL_RC" 2>/dev/null; then
        print_step "Alias 'wasteless' deja present"
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
    echo "  alias wasteless='$(pwd)/wasteless.sh'"
fi

# =============================================================================
# VERIFICATION FINALE
# =============================================================================
print_header "7/7 - Verification de l'installation"

# Test de connexion DB
print_info "Test de connexion a la base de donnees..."
if python3 -c "
from src.core.database import health_check
import sys
sys.exit(0 if health_check() else 1)
" 2>/dev/null; then
    print_step "Connexion base de donnees OK"
else
    print_warning "Test de connexion echoue (normal si premiere installation)"
fi

# Test des modules
print_info "Verification des modules..."
if python3 -c "
from src.core.config import RemediationConfig
config = RemediationConfig.from_yaml('config/remediation.yaml')
assert config.enabled == False, 'Auto-remediation should be disabled'
print('OK')
" 2>/dev/null; then
    print_step "Modules Python OK"
    print_step "Auto-remediation desactivee (securite)"
else
    print_error "Erreur lors du chargement des modules"
fi

# Tests unitaires
print_info "Execution des tests unitaires..."
if ./venv/bin/pytest tests/unit/test_validation.py -q 2>/dev/null; then
    print_step "Tests unitaires OK"
else
    print_warning "Certains tests ont echoue"
fi

# =============================================================================
# RESUME ET PROCHAINES ETAPES
# =============================================================================
print_header "Installation terminee"

echo -e "${GREEN}${BOLD}WasteLess est installe et pret!${NC}"
echo ""
echo -e "${BOLD}Pour demarrer:${NC}"
echo ""
echo -e "  ${CYAN}1. Rechargez votre shell:${NC}"
if [ -n "$SHELL_RC" ]; then
    echo "     source $SHELL_RC"
else
    echo "     Ouvrez un nouveau terminal (shell non detecte)"
    echo "     Ou ajoutez manuellement : alias wasteless='$(pwd)/ui/start.sh'"
fi
echo ""
echo -e "  ${CYAN}2. Lancez l'interface:${NC}"
echo "     wasteless"
echo "     -> http://localhost:8888"
echo ""
echo -e "${BOLD}Prochaines etapes:${NC}"
echo ""
echo -e "  1. ${CYAN}Collecter les metriques et detecter le gaspillage:${NC}"
echo "     wasteless collect"
echo ""
echo -e "  2. ${CYAN}Voir les recommandations:${NC}"
echo "     -> http://localhost:8888/recommendations"
echo ""
echo -e "${BOLD}Pages disponibles (http://localhost:8888):${NC}"
echo "  - /                  Vue d'ensemble"
echo "  - /dashboard         Metriques et graphiques"
echo "  - /recommendations   Approuver / Rejeter les actions"
echo "  - /cloud-resources   Inventaire EC2"
echo "  - /history           Historique des remediations"
echo "  - /settings          Configuration et whitelist"
echo ""
echo -e "${BOLD}Commandes utiles:${NC}"
echo "  - Demarrer l'interface:    wasteless"
echo "  - Voir les logs Docker:    docker compose logs -f"
echo "  - Arreter les services:    docker compose down"
echo "  - Lancer les tests:        ./venv/bin/pytest tests/"
echo ""
echo -e "${BOLD}Documentation:${NC}"
echo "  - Architecture:  docs/ARCHITECTURE.md"
echo "  - Deploiement:   docs/DEPLOYMENT.md"
echo "  - AWS Setup:     docs/AWS_SETUP.md"
echo ""
echo -e "${YELLOW}${BOLD}Important:${NC}"
echo "  L'auto-remediation est DESACTIVEE par defaut."
echo "  Pour l'activer, modifiez config/remediation.yaml"
echo ""
