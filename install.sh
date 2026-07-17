#!/bin/bash
#
# WasteLess - Script d'installation automatique
#
# Usage: ./install.sh [-q|--quiet]
#
# Options:
#   -q, --quiet    Masque la sortie detaillee de chaque commande
#   -h, --help     Affiche cette aide
#
# Ce script configure automatiquement l'environnement WasteLess:
# - Verifie les prerequis (Python, Docker; AWS CLI reste optionnel)
# - Cree l'environnement virtuel Python
# - Installe les dependances
# - Configure la base de donnees
# - Guide la configuration AWS
#

set -e  # Exit on error

VERBOSE=1

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

silence() {
    if [ $VERBOSE -eq 1 ]; then
        "$@"
    else
        "$@" &>/dev/null
    fi
}

sed_inplace() {
    # sed -i portable: GNU (Linux/WSL) vs BSD (macOS)
    if sed --version &>/dev/null; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# Pose KEY=VALUE dans un fichier .env : remplace la ligne si la cle existe,
# l'ajoute sinon. Delimiteur sed `|` : absent des ARN et des cles AWS.
set_env_kv() {
    local file="$1" key="$2" value="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed_inplace "s|^${key}=.*|${key}=${value}|" "$file"
    else
        echo "${key}=${value}" >> "$file"
    fi
}

print_verbose() {
    if [ $VERBOSE -eq 1 ]; then
        echo -e "  ${CYAN}»${NC} $1"
    fi
}

# =============================================================================
# DETECTION DE L'HOTE (OS, init system, WSL) — pilote la remediation systeme
# =============================================================================
detect_host() {
    OS_NAME="$(uname -s)"
    ARCH="$(uname -m)"
    INIT_SYSTEM="$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ' || echo unknown)"
    IS_WSL=0
    grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=1

    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_ID_LIKE="${ID_LIKE:-}"
        OS_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
        OS_PRETTY="${PRETTY_NAME:-$OS_ID}"
    else
        OS_ID="$( [ "$OS_NAME" = "Darwin" ] && echo macos || echo unknown )"
        OS_ID_LIKE=""
        OS_CODENAME=""
        OS_PRETTY="$OS_NAME"
    fi

    print_info "Systeme: $OS_PRETTY ($ARCH) — init: $INIT_SYSTEM"
    [ "$IS_WSL" -eq 1 ] && print_info "Environnement WSL detecte"
    # return 0 explicite : appele "nu" sous set -e, un dernier test a 1
    # (machine non-WSL) ferait sinon planter tout le script.
    return 0
}

has_systemd() {
    [ "$INIT_SYSTEM" = "systemd" ] && command -v systemctl >/dev/null 2>&1
}

# Execute une commande en root : direct si deja root, sinon via sudo.
sudo_cmd() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

# Demande l'accord de l'utilisateur avant une modification systeme.
# Toujours OK en mode --install-system-deps ou -y (non-interactif).
confirm_system_change() {
    local message="$1"
    if [ "$AUTO_INSTALL_DEPS" -eq 1 ] || [ "$ASSUME_YES" -eq 1 ]; then
        return 0
    fi
    echo ""
    print_warning "$message"
    read -p "Autoriser cette modification systeme ? (o/N): " answer
    [[ "$answer" =~ ^[OoYy]$ ]]
}

# Wrapper Docker Compose : plugin v2 (docker compose) ou binaire v1 legacy.
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        return 127
    fi
}

# =============================================================================
# CHARGEMENT SUR DE .ENV (sans `source` — un mot de passe avec $, `, #, espace
# casserait l'install ou executerait du shell arbitraire)
# =============================================================================
get_env_var() {
    local key="$1" file="${2:-.env}"
    grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2-
}

load_env_file() {
    local env_file="${1:-.env}"
    if [ ! -f "$env_file" ]; then
        print_error "Fichier $env_file introuvable"
        return 1
    fi
    DB_HOST="$(get_env_var DB_HOST "$env_file")"
    DB_PORT="$(get_env_var DB_PORT "$env_file")"
    DB_NAME="$(get_env_var DB_NAME "$env_file")"
    DB_USER="$(get_env_var DB_USER "$env_file")"
    DB_PASSWORD="$(get_env_var DB_PASSWORD "$env_file")"
    AWS_REGION="$(get_env_var AWS_REGION "$env_file")"
    export DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD AWS_REGION
}

# =============================================================================
# REMEDIATION DOCKER : installe (depuis les depots officiels) et/ou demarre
# =============================================================================
install_docker_debian_ubuntu() {
    print_info "Installation de Docker Engine depuis le depot officiel Docker..."
    local distro="debian"
    if [ "$OS_ID" = "ubuntu" ] || echo "$OS_ID_LIKE" | grep -q ubuntu; then
        distro="ubuntu"
    fi
    local codename="${OS_CODENAME}"
    if [ -z "$codename" ]; then
        print_error "Codename de distribution introuvable — installation Docker impossible"
        return 1
    fi
    sudo_cmd apt-get update
    sudo_cmd apt-get install -y ca-certificates curl
    sudo_cmd install -m 0755 -d /etc/apt/keyrings
    sudo_cmd curl -fsSL "https://download.docker.com/linux/${distro}/gpg" -o /etc/apt/keyrings/docker.asc
    sudo_cmd chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${distro} ${codename} stable" \
        | sudo_cmd tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo_cmd apt-get update
    sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_fedora() {
    print_info "Installation de Docker Engine depuis le depot officiel Fedora..."
    sudo_cmd dnf -y install dnf-plugins-core
    sudo_cmd dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo 2>/dev/null \
        || sudo_cmd dnf config-manager addrepo --from-repofile https://download.docker.com/linux/fedora/docker-ce.repo
    sudo_cmd dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_arch() {
    print_info "Installation de Docker via pacman..."
    sudo_cmd pacman -Sy --noconfirm docker docker-compose
}

install_docker() {
    if [ "$IS_WSL" -eq 1 ]; then
        print_error "Docker absent dans WSL — pas d'installation automatique."
        print_info "Installez Docker Desktop cote Windows, activez l'integration WSL2, puis relancez."
        return 1
    fi
    if [ "$OS_ID" = "macos" ]; then
        print_error "Docker absent sur macOS — pas d'installation automatique."
        print_info "Installez Docker Desktop (brew install --cask docker) puis relancez."
        return 1
    fi
    if ! confirm_system_change "Docker est absent. Le script peut l'installer depuis les depots officiels."; then
        print_error "Docker requis. Relancez avec --install-system-deps pour l'installer automatiquement."
        return 1
    fi
    case "$OS_ID" in
        ubuntu|debian) install_docker_debian_ubuntu ;;
        fedora|rhel|centos|rocky|almalinux) install_docker_fedora ;;
        arch|manjaro|endeavouros) install_docker_arch ;;
        *)
            if command -v apt-get >/dev/null 2>&1; then install_docker_debian_ubuntu
            elif command -v dnf >/dev/null 2>&1; then install_docker_fedora
            elif command -v pacman >/dev/null 2>&1; then install_docker_arch
            else
                print_error "Distribution non supportee pour l'installation automatique: $OS_PRETTY"
                return 1
            fi
            ;;
    esac
}

start_docker() {
    if docker info >/dev/null 2>&1; then
        return 0
    fi
    if [ "$IS_WSL" -eq 1 ]; then
        print_error "Docker installe mais daemon inaccessible depuis WSL"
        print_info "Ouvrez Docker Desktop cote Windows et activez l'integration WSL2."
        return 1
    fi
    if [ "$OS_ID" = "macos" ]; then
        print_error "Docker installe mais non demarre — lancez Docker Desktop."
        return 1
    fi
    if has_systemd; then
        if confirm_system_change "Docker est arrete. Le script peut demarrer et activer le service."; then
            print_info "Demarrage du service Docker..."
            sudo_cmd systemctl enable --now docker || true
        fi
    else
        print_warning "systemd absent — impossible de demarrer Docker automatiquement."
    fi
    if docker info >/dev/null 2>&1; then
        return 0
    fi
    # Daemon up mais l'utilisateur courant n'est pas dans le groupe docker
    if sudo docker info >/dev/null 2>&1; then
        print_warning "Docker fonctionne avec sudo mais pas pour l'utilisateur courant"
        if confirm_system_change "Ajouter '$USER' au groupe docker"; then
            sudo_cmd usermod -aG docker "$USER"
            print_warning "Utilisateur ajoute au groupe docker."
            # Le shell courant ne recoit le nouveau groupe qu'a la prochaine
            # connexion, mais `sg docker` lit /etc/group immediatement : si ca
            # marche, on relance l'installation nous-memes plutot que de
            # demander a l'utilisateur de le faire (le garde-fou env evite
            # toute boucle si la relance ne suffit pas).
            if [ -z "${WASTELESS_SG_RELAUNCHED:-}" ] \
                && command -v sg >/dev/null 2>&1 \
                && sg docker -c "docker info" >/dev/null 2>&1; then
                print_info "Docker operationnel via le nouveau groupe — relance automatique de l'installation..."
                export WASTELESS_SG_RELAUNCHED=1
                exec sg docker -c "$(printf '%q' "$0")$INSTALL_ARGS"
            fi
            DOCKER_GROUP_PENDING=1
            print_info "Ouvrez un nouveau shell (ou 'newgrp docker') puis relancez ./install.sh"
        fi
        return 1
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Store d'images corrompu : typiquement une migration docker.io -> docker-ce
# (apt purge) qui supprime les donnees de /var/lib/containerd en laissant la
# base de metadonnees. Deux signatures observees :
#   - "blob not found" : les metadonnees referencent des blobs disparus ;
#     `docker rmi -f <sha>` purge chaque entree orpheline (sans risque, les
#     donnees de ces images sont deja perdues) — le sha signale est l'ID de
#     l'image cassee.
#   - "failed to create prepare snapshot dir" : le repertoire de travail des
#     snapshots a ete efface ; le demon le recree a son demarrage.
# ---------------------------------------------------------------------------
docker_store_healthy() {
    docker images >/dev/null 2>&1
}

check_docker_store() {
    local out blob i
    out="$(docker images 2>&1)" && return 0
    if ! echo "$out" | grep -q 'blob not found'; then
        # Autre echec (daemon arrete, droits) : deja gere par start_docker.
        return 0
    fi
    print_warning "Store d'images Docker corrompu (blob manquant dans /var/lib/containerd)"
    if ! confirm_system_change "Le script peut purger les metadonnees des images orphelines (leurs donnees sont deja perdues)."; then
        print_info "Reparation manuelle : docker rmi -f <sha256 signale par 'docker images'>, puis relancez ./install.sh"
        return 1
    fi
    for i in $(seq 1 20); do
        if out="$(docker images 2>&1)"; then
            print_step "Store d'images Docker repare ($((i - 1)) image(s) orpheline(s) purgee(s))"
            return 0
        fi
        echo "$out" | grep -q 'blob not found' || break
        blob="$(echo "$out" | grep -oE 'sha256:[a-f0-9]{64}' | head -n1)"
        [ -n "$blob" ] || break
        print_info "Purge des metadonnees orphelines : $blob"
        silence docker rmi -f "$blob" || break
    done
    print_error "Store Docker toujours corrompu apres purge"
    print_info "Redemarrez le demon (sudo systemctl restart docker) puis relancez ./install.sh"
    return 1
}

restart_docker_daemon() {
    local i
    has_systemd || return 1
    confirm_system_change "Le script peut redemarrer les demons Docker et containerd pour recreer leurs repertoires de travail." || return 1
    print_info "Redemarrage des demons containerd et Docker..."
    # C'est containerd (service separe avec Docker CE) qui recree le
    # repertoire des snapshots a son demarrage — redemarrer docker seul ne
    # suffit pas. Unite absente (Docker Desktop, docker.io ancien) : tolere.
    if systemctl list-unit-files containerd.service >/dev/null 2>&1; then
        sudo_cmd systemctl restart containerd || return 1
    fi
    sudo_cmd systemctl restart docker || return 1
    for i in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

ensure_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        install_docker || return 1
    fi
    if ! start_docker; then
        # Ne pas dire "non fonctionnel" quand le daemon tourne et que seul le
        # groupe docker manque au shell courant — le message final du bloc
        # MISSING_DEPS donne alors la vraie marche a suivre.
        if [ "$DOCKER_GROUP_PENDING" -eq 0 ]; then
            print_error "Docker installe mais non fonctionnel"
        fi
        return 1
    fi
    print_step "Docker detecte et fonctionnel"
    # Detecter un store corrompu ici, pas a l'etape 4 : l'utilisateur a la
    # remediation avant d'avoir attendu toute la partie Python/config.
    check_docker_store || return 1
    if ! compose version >/dev/null 2>&1; then
        print_error "Docker Compose introuvable — installez le plugin docker-compose-plugin"
        return 1
    fi
    print_step "Docker Compose detecte"
    return 0
}

# =============================================================================
# REMEDIATION STEAMPIPE : installe le CLI + plugin AWS apres accord utilisateur
# (les detecteurs 7-14 de wasteless.sh en dependent : ELB, NAT, VPC, gp2->gp3,
# AMI, RDS ; sans eux chaque collecte est marquee "partielle" sur le dashboard)
# =============================================================================
steampipe_aws_plugin_present() {
    # Le CLI seul ne suffit pas : les detecteurs interrogent AWS via le
    # plugin turbot/aws, installe sous ~/.steampipe (sans sudo).
    ls "$HOME"/.steampipe/plugins/hub.steampipe.io/plugins/turbot/aws* >/dev/null 2>&1
}

install_steampipe_binary() {
    if [ "$OS_ID" = "macos" ]; then
        if ! check_command brew; then
            print_warning "Homebrew requis pour installer Steampipe sur macOS (https://brew.sh)"
            return 1
        fi
        print_info "Installation de Steampipe via Homebrew..."
        brew install turbot/tap/steampipe
    else
        # Script officiel Turbot : depose le binaire dans /usr/local/bin (sudo).
        # curl d'abord dans une variable : sous set -e, un echec reseau dans
        # $(...) donnerait un script vide que `sh -c` executerait avec succes.
        print_info "Installation de Steampipe via le script officiel (steampipe.io)..."
        local installer
        if ! installer="$(curl -fsSL https://steampipe.io/install/steampipe.sh)"; then
            print_error "Telechargement du script d'installation Steampipe echoue (reseau ?)"
            return 1
        fi
        sudo_cmd /bin/sh -c "$installer"
    fi
}

ensure_steampipe() {
    # accord deja donne pour "CLI + plugin" -> pas de 2e question pour le plugin
    local approved=0
    if ! check_command steampipe; then
        print_warning "Steampipe non trouve : les detecteurs ELB/NAT/VPC/gp2/AMI/RDS seraient ignores (collecte partielle)"
        if ! confirm_system_change "Steampipe est absent. Le script peut installer le CLI et son plugin AWS."; then
            if [ "$OS_ID" = "macos" ]; then
                print_info "Installation manuelle: brew install turbot/tap/steampipe && steampipe plugin install aws"
            else
                print_info "Installation manuelle: sudo /bin/sh -c \"\$(curl -fsSL https://steampipe.io/install/steampipe.sh)\" && steampipe plugin install aws"
            fi
            return 1
        fi
        approved=1
        if ! install_steampipe_binary; then
            print_error "Installation de Steampipe echouee, la collecte restera partielle (https://steampipe.io/downloads)"
            return 1
        fi
        # Le shell courant peut ne pas encore voir le binaire (ex: premier
        # paquet Homebrew installe), meme prepend que wasteless.sh.
        export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        if ! check_command steampipe; then
            print_error "Steampipe installe mais introuvable dans le PATH. Ouvrez un nouveau terminal puis relancez ./install.sh"
            return 1
        fi
    fi
    print_step "Steampipe detecte"

    if steampipe_aws_plugin_present; then
        print_step "Plugin AWS de Steampipe detecte"
        return 0
    fi
    if [ "$approved" -eq 0 ]; then
        print_warning "Plugin AWS de Steampipe absent : les detecteurs Steampipe echoueraient"
        if ! confirm_system_change "Le script peut installer le plugin AWS de Steampipe (sous ~/.steampipe, sans sudo)."; then
            print_info "Installation manuelle: steampipe plugin install aws"
            return 1
        fi
    fi
    print_info "Installation du plugin AWS de Steampipe..."
    if steampipe plugin install aws; then
        print_step "Plugin AWS de Steampipe installe"
        return 0
    fi
    print_error "Installation du plugin AWS echouee. Reessayez: steampipe plugin install aws"
    return 1
}

# =============================================================================
# ATTENTE POSTGRESQL ROBUSTE + DUMP DIAGNOSTIC EN CAS D'ECHEC
# =============================================================================
check_port_conflict() {
    local port="${DB_PORT:-5432}"
    if command -v ss >/dev/null 2>&1; then
        if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$"; then
            print_warning "Le port ${port} semble deja utilise sur l'hote"
            print_info "Si le demarrage echoue, changez DB_PORT dans .env (ex: DB_PORT=5433)"
        fi
    fi
    return 0
}

postgres_debug_dump() {
    print_error "PostgreSQL indisponible apres attente"
    echo ""
    print_info "Etat Docker Compose:"; compose ps 2>/dev/null || true
    echo ""
    print_info "Logs PostgreSQL (120 dernieres lignes):"
    compose logs --tail=120 postgres 2>/dev/null || docker logs --tail=120 wasteless-postgres 2>/dev/null || true
    echo ""
    print_info "Etat du conteneur:"
    docker inspect wasteless-postgres \
        --format='Status={{.State.Status}} ExitCode={{.State.ExitCode}} Error={{.State.Error}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        2>/dev/null || true
    echo ""
    print_info "Causes probables: port ${DB_PORT:-5432} occupe · DB_PASSWORD different du volume existant · volume postgres corrompu"
}

wait_for_postgres() {
    local timeout="${1:-120}" elapsed=0
    print_info "Attente de la disponibilite de PostgreSQL..."
    while [ "$elapsed" -lt "$timeout" ]; do
        if docker exec wasteless-postgres pg_isready -U "${DB_USER:-wasteless}" -d "${DB_NAME:-wasteless}" >/dev/null 2>&1; then
            print_step "PostgreSQL est pret"
            return 0
        fi
        # Sortie anticipee si le conteneur est mort (crash au boot)
        if docker inspect wasteless-postgres >/dev/null 2>&1; then
            local running
            running="$(docker inspect -f '{{.State.Running}}' wasteless-postgres 2>/dev/null || echo false)"
            if [ "$running" != "true" ]; then
                postgres_debug_dump
                return 1
            fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    postgres_debug_dump
    return 1
}

# =============================================================================
# ARGUMENTS
# =============================================================================
# AUTO_INSTALL_DEPS : autorise l'installation des prerequis systeme manquants
#   (Docker Engine) via le gestionnaire de paquets de la distro.
# ASSUME_YES        : mode non-interactif, valide toutes les modifs systeme.
# DOCTOR_ONLY       : diagnostic seul, aucune modification (sort apres 1/7).
AUTO_INSTALL_DEPS=0
ASSUME_YES=0
DOCTOR_ONLY=0
SETUP_SCHEDULE=1  # installe la collecte automatique au niveau OS; --no-schedule pour couper
DOCKER_GROUP_PENDING=0  # utilisateur ajoute au groupe docker mais shell pas encore relance

# Arguments originaux, shell-quotes, pour la relance automatique via
# `sg docker` (voir start_docker) — les flags sont simples mais on quote
# quand meme par principe.
INSTALL_ARGS=""
for arg in "$@"; do INSTALL_ARGS="$INSTALL_ARGS $(printf '%q' "$arg")"; done

for arg in "$@"; do
    case "$arg" in
        -q|--quiet) VERBOSE=0 ;;
        -v|--verbose) VERBOSE=1 ;;  # rétrocompatibilité
        --install-system-deps) AUTO_INSTALL_DEPS=1 ;;
        -y|--yes) ASSUME_YES=1 ;;
        --doctor) DOCTOR_ONLY=1 ;;
        --no-schedule) SETUP_SCHEDULE=0 ;;
        -h|--help)
            echo "Usage: ./install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -q, --quiet              Masque la sortie detaillee des commandes"
            echo "  --doctor                 Diagnostic uniquement, aucune modification systeme"
            echo "  --install-system-deps    Installe les prerequis systeme manquants (Docker)"
            echo "  --no-schedule            N'installe pas la collecte automatique (OS scheduler)"
            echo "  -y, --yes                Mode non-interactif (valide les modifs systeme)"
            echo "  -h, --help               Affiche cette aide"
            exit 0
            ;;
        *) echo "Option inconnue: $arg"; echo "Usage: ./install.sh [-q|--quiet] [--doctor] [--install-system-deps] [-y]"; exit 1 ;;
    esac
done

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
if [ $VERBOSE -eq 1 ]; then
    echo -e "${CYAN}Mode verbose active — sortie detaillee des commandes${NC}"
fi
echo ""

# =============================================================================
# VERIFICATION DES PREREQUIS
# =============================================================================
print_header "1/7 - Verification des prerequis"

MISSING_DEPS=0

# Detection de l'hote (OS, init system, WSL) — pilote la remediation systeme
detect_host

# WSL : le projet doit vivre dans le systeme de fichiers Linux (~), pas dans
# le disque Windows monte (/mnt/c/...) ou les permissions et les perfs cassent.
if [ "$IS_WSL" -eq 1 ]; then
    case "$PWD" in
        /mnt/*)
            print_error "Le projet est sur le disque Windows ($PWD)"
            print_info "Clonez-le dans le systeme de fichiers Linux, par exemple:"
            print_info "  cd ~ && git clone <repo> && cd wasteless && ./install.sh"
            exit 1
            ;;
    esac
fi

# Python 3.11+
if check_command python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -gt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; }; then
        print_step "Python $PYTHON_VERSION detecte"
    else
        print_error "Python 3.11+ requis (trouve: $PYTHON_VERSION)"
        MISSING_DEPS=1
    fi
else
    print_error "Python3 non trouve"
    MISSING_DEPS=1
fi

# Docker + Docker Compose : couche de remediation active.
#   --doctor              -> diagnostic seul, aucune modif systeme
#   --install-system-deps -> installe Docker depuis les depots officiels si absent
#   defaut interactif     -> demande confirmation avant toute modif systeme
if [ "$DOCTOR_ONLY" -eq 1 ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        print_step "Docker detecte et fonctionnel"
        docker_store_healthy \
            || { print_warning "Store d'images Docker corrompu (relancez sans --doctor pour le reparer)"; MISSING_DEPS=1; }
        compose version >/dev/null 2>&1 && print_step "Docker Compose detecte" \
            || { print_warning "Docker Compose introuvable (plugin docker-compose-plugin)"; MISSING_DEPS=1; }
    elif command -v docker >/dev/null 2>&1; then
        print_warning "Docker installe mais non demarre (relancez sans --doctor pour le demarrer)"
        MISSING_DEPS=1
    else
        print_warning "Docker absent (relancez avec --install-system-deps pour l'installer)"
        MISSING_DEPS=1
    fi
elif ! ensure_docker; then
    MISSING_DEPS=1
fi

# uv (optionnel mais recommande — installations atomiques, 10-100x plus rapide)
if check_command uv; then
    UV_VERSION=$(uv --version 2>&1 | head -1)
    print_step "uv detecte ($UV_VERSION) — installations atomiques activees"
    USE_UV=1
else
    print_warning "uv non trouve (optionnel) — utilisation de pip"
    print_info "Installez uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    USE_UV=0
fi

# AWS CLI (optionnel mais recommande)
if check_command aws; then
    print_step "AWS CLI detecte"
else
    print_warning "AWS CLI non trouve (optionnel)"
    print_info "Installez AWS CLI: https://aws.amazon.com/cli/"
fi

# Steampipe (les detecteurs 7-14 en dependent : ELB, NAT gateways, VPC,
# migration gp2->gp3, AMI orphelines, RDS ; sans lui chaque collecte est
# marquee partielle sur le dashboard). Absent -> proposition d'installation
# automatique apres accord utilisateur, comme Docker.
if [ "$DOCTOR_ONLY" -eq 1 ]; then
    if ! check_command steampipe; then
        print_warning "Steampipe absent (relancez sans --doctor pour l'installer), collecte partielle"
    elif ! steampipe_aws_plugin_present; then
        print_step "Steampipe detecte"
        print_warning "Plugin AWS de Steampipe absent (relancez sans --doctor pour l'installer)"
    else
        print_step "Steampipe + plugin AWS detectes"
    fi
else
    # Optionnel : un refus ou un echec n'empeche pas l'installation de continuer.
    ensure_steampipe || true
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
    # Cas particulier : tout est installe, seul le groupe docker n'est pas
    # encore actif dans ce shell. Le conseil generique "apt install python3"
    # serait hors sujet et a deja perdu des utilisateurs — donner uniquement
    # la vraie action.
    if [ "$DOCKER_GROUP_PENDING" -eq 1 ]; then
        print_error "Docker est installe et demarre, mais ce shell n'a pas encore le groupe docker."
        print_info "Ouvrez un nouveau terminal (ou lancez: newgrp docker), puis relancez ./install.sh"
        exit 1
    fi
    print_error "Certains prerequis sont manquants. Installez-les et reexecutez ce script."
    if [ "$(uname)" = "Darwin" ]; then
        if check_command brew; then
            print_info "Sur macOS, tout s'installe en une commande: brew bundle"
        else
            print_info "Installez Homebrew (https://brew.sh) puis lancez: brew bundle"
        fi
    elif check_command apt; then
        print_info "Sur Ubuntu/Debian/WSL:"
        print_info "  sudo apt update && sudo apt install -y python3 python3-venv python3-pip git"
        if grep -qi microsoft /proc/version 2>/dev/null; then
            print_info "Docker sous WSL: installez Docker Desktop sur Windows avec le"
            print_info "backend WSL2, puis activez l'integration Ubuntu dans"
            print_info "Settings > Resources > WSL integration"
        fi
    fi
    exit 1
fi

echo ""
print_step "Tous les prerequis sont satisfaits"

# Les venvs vivent en .nosync (voir create_venv plus bas) : ~20 000 fichiers
# qu'iCloud stocke en "dataless". `git status` scanne les untracked et doit
# materialiser chaque stat -> plus de 2 min par appel. Le fsmonitor integre de
# git (>= 2.37) + le cache des untracked ramenent ca a l'instantane. Config
# locale (.git/config), sans effet sur les autres clones.
if git rev-parse --is-inside-work-tree &> /dev/null; then
    git config core.fsmonitor true
    git config core.untrackedCache true
    print_step "git fsmonitor + untracked cache actives (git status rapide)"
fi

# Mode diagnostic : on s'arrete ici, avant toute creation de venv / conteneur.
if [ "$DOCTOR_ONLY" -eq 1 ]; then
    echo ""
    print_step "Diagnostic termine (--doctor) — aucune modification effectuee"
    exit 0
fi

# =============================================================================
# CREATION DE L'ENVIRONNEMENT VIRTUEL
# =============================================================================
print_header "2/7 - Configuration de l'environnement Python"

create_venv() {
    local path="$1"
    # Sur macOS, le venv est cree en <path>.nosync + symlink <path>.
    # Le suffixe .nosync empeche iCloud Drive de synchroniser le venv, ce qui
    # evite les copies de conflit ("venv 2", "bin 2") qui corrompent
    # l'environnement quand le projet vit dans Documents ou Desktop.
    if [ "$(uname)" = "Darwin" ]; then
        local real="${path}.nosync"
        rm -rf "$real"
        if [ $USE_UV -eq 1 ]; then
            uv venv "$real"
        else
            python3 -m venv "$real"
        fi
        ln -sfn "$(basename "$real")" "$path"
    else
        if [ $USE_UV -eq 1 ]; then
            uv venv "$path"
        else
            python3 -m venv "$path"
        fi
    fi
}

install_deps() {
    local venv_path="$1"
    local req_file="$2"
    local quiet_opt
    quiet_opt=$([ $VERBOSE -eq 0 ] && echo "-q" || echo "")
    if [ $USE_UV -eq 1 ]; then
        uv pip install --python "$venv_path/bin/python3" -r "$req_file" $quiet_opt
    else
        "$venv_path/bin/pip" install --upgrade pip $quiet_opt
        "$venv_path/bin/pip" install -r "$req_file" $quiet_opt
    fi
}

if [ -d "venv" ]; then
    # Verifier que le venv n'est pas corrompu (python valide)
    if ! venv/bin/python3 -c "import sys" &> /dev/null; then
        print_warning "Environnement virtuel corrompu detecte — recreation automatique"
        rm -rf venv
        create_venv venv
        print_step "Environnement virtuel recree"
    else
        print_warning "Environnement virtuel existant detecte"
        read -p "Voulez-vous le recreer? (o/N): " RECREATE_VENV
        if [[ "$RECREATE_VENV" =~ ^[Oo]$ ]]; then
            rm -rf venv
            create_venv venv
            print_step "Environnement virtuel recree"
        else
            print_step "Environnement virtuel conserve"
        fi
    fi
else
    create_venv venv
    print_step "Environnement virtuel cree"
fi

# Activation et installation des dependances
source venv/bin/activate
print_step "Environnement virtuel active"

PIP_OPT=$([ $VERBOSE -eq 0 ] && echo "-q" || echo "")
print_verbose "installation des dependances (requirements.lock)"
# On installe depuis le lock (versions epinglees, reproductibles), pas depuis
# requirements.txt (contraintes >= flottantes). Regenerer le lock apres avoir
# edite requirements.txt :  uv pip compile requirements.txt --universal \
#   --output-file requirements.lock
install_deps venv requirements.lock

# Outils de qualite (ruff, black, mypy, shellcheck) epingles dans le meme
# venv : `make lint` fonctionne des l'installation et tourne exactement les
# versions de la CI (qui installe depuis le meme lock).
print_verbose "installation des outils de lint (requirements-dev.lock)"
install_deps venv requirements-dev.lock

# Backend (src/core, src/detectors, ...) installe en editable dans le venv
# racine (pyproject.toml). Rend `core`, `detectors`, etc. importables partout
# sans sys.path.insert() en tete de chaque module : `python3 src/detectors/
# ec2_idle.py` (ce que fait wasteless.sh) trouve `core` via le package installe.
print_verbose "installation du backend en editable dans venv (pyproject.toml)"
if [ $USE_UV -eq 1 ]; then
    silence uv pip install --python venv/bin/python3 -e .
else
    silence venv/bin/pip install -e . $PIP_OPT
fi
print_step "Dependances Python installees"

# =============================================================================
# CONFIGURATION DU FICHIER .ENV
# =============================================================================
print_header "3/7 - Configuration de l'application"

# Politique de remediation locale (non versionnee, comme .env)
if [ ! -f "config/remediation.yaml" ]; then
    cp config/remediation.yaml.template config/remediation.yaml
    print_step "config/remediation.yaml cree depuis le template (remediation desactivee)"
fi

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
    # Charset restreint volontairement : le mot de passe transite par .env (lu
    # sans `source`) et par docker-compose (substitution ${DB_PASSWORD}). On
    # exclut $, `, ", ', #, espace et \ qui casseraient l'un ou l'autre.
    # Double saisie : la frappe est masquee (read -s), une faute de frappe
    # invisible ici donnerait un mot de passe irrecuperable au premier
    # demarrage (le volume postgres est initialise avec).
    while true; do
        read -sp "Creez un mot de passe pour la base de donnees: " DB_PASSWORD
        echo ""
        if [ ${#DB_PASSWORD} -lt 8 ]; then
            print_error "Le mot de passe doit contenir au moins 8 caracteres"
            continue
        elif [[ ! "$DB_PASSWORD" =~ ^[A-Za-z0-9_@%+=:,.~-]+$ ]]; then
            print_error "Caracteres autorises: lettres, chiffres et _ @ % + = : , . ~ -"
            continue
        fi
        read -sp "Confirmez le mot de passe: " DB_PASSWORD_CONFIRM
        echo ""
        if [ "$DB_PASSWORD" != "$DB_PASSWORD_CONFIRM" ]; then
            print_error "Les deux saisies ne correspondent pas, recommencez"
            continue
        fi
        unset DB_PASSWORD_CONFIRM
        break
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

    # Connexion AWS : choix ferme plutot que 4 questions ouvertes. Avant,
    # tout laisser vide etait accepte sans un mot ("optionnel") et donnait
    # un dashboard vide + une collecte en echec silencieux — le report est
    # maintenant un choix explicite dont la consequence est annoncee.
    echo ""
    echo "  Connexion du compte AWS"
    echo "  Wasteless lit votre compte via un role IAM read-only, a creer en une etape"
    echo "  avec le template fourni (dossier onboarding/, CloudFormation ou Terraform)."
    echo ""
    echo "    1) Les roles sont crees, j'ai leurs ARN sous la main -> je les colle maintenant"
    echo "       (recommande si vous avez deja suivi docs/CTO_QUICKSTART.md)"
    echo "    2) Pas encore -> terminer l'installation d'abord. Le navigateur s'ouvrira"
    echo "       sur le guide de connexion (/setup) : creation des roles en un clic dans"
    echo "       votre console AWS, champs pre-remplis, sans revenir au terminal."
    echo "       C'est le choix par defaut."
    echo "    3) Coller des cles d'acces IAM directes (deconseille : wasteless obtient"
    echo "       alors tous les droits de ces cles, au lieu d'etre limite au role read-only)"
    AWS_ROLE_ARN=""
    AWS_WRITE_ROLE_ARN=""
    AWS_EXTERNAL_ID=""
    AWS_ACCESS_KEY_ID=""
    AWS_SECRET_ACCESS_KEY=""
    AWS_CONFIGURED=0

    while true; do
        read -p "  Votre choix [1/2/3, defaut 2]: " AWS_SETUP_CHOICE
        case "$AWS_SETUP_CHOICE" in
            1)
                read -p "ARN du role read-only (arn:aws:iam::${AWS_ACCOUNT_ID}:role/wasteless-readonly): " AWS_ROLE_ARN
                while [[ ! "$AWS_ROLE_ARN" =~ ^arn:aws:iam::[0-9]{12}:role/.+$ ]]; do
                    print_error "Format attendu: arn:aws:iam::<12 chiffres>:role/<nom>"
                    read -p "ARN du role read-only: " AWS_ROLE_ARN
                done
                read -p "ARN du role remediation (optionnel, Entree pour passer): " AWS_WRITE_ROLE_ARN
                read -p "ExternalId (optionnel): " AWS_EXTERNAL_ID
                echo ""
                print_info "Credentials source pour assumer le role (laissez vide si ~/.aws est deja configure)"
                read -p "AWS Access Key ID: " AWS_ACCESS_KEY_ID
                if [ -n "$AWS_ACCESS_KEY_ID" ]; then
                    read -sp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
                    echo ""
                fi
                AWS_CONFIGURED=1
                ;;
            3)
                read -p "AWS Access Key ID: " AWS_ACCESS_KEY_ID
                while [ -z "$AWS_ACCESS_KEY_ID" ]; do
                    read -p "AWS Access Key ID (requis pour ce choix): " AWS_ACCESS_KEY_ID
                done
                read -sp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
                echo ""
                AWS_CONFIGURED=1
                ;;
            *)
                print_step "Connexion AWS reportee — elle se fera dans le navigateur en fin d'installation (/setup)"
                break
                ;;
        esac

        # Validation immediate (STS + AssumeRole si role fourni) : le seul
        # moment ou corriger une faute de frappe est facile, c'est pendant
        # que l'utilisateur est encore devant son terminal. Les credentials
        # passent par des variables WL_CHECK_* dediees pour ne pas polluer
        # la chaine boto3 par defaut (~/.aws) quand ils sont vides.
        print_info "Validation de la connexion AWS..."
        if AWS_VALIDATION_OUTPUT=$(WL_CHECK_KEY="$AWS_ACCESS_KEY_ID" \
            WL_CHECK_SECRET="$AWS_SECRET_ACCESS_KEY" \
            WL_CHECK_REGION="$AWS_REGION" \
            WL_CHECK_ROLE_ARN="$AWS_ROLE_ARN" \
            WL_CHECK_EXTERNAL_ID="$AWS_EXTERNAL_ID" \
            venv/bin/python3 - 2>&1 <<'PYCHECK'
import os
import boto3

session = boto3.Session(
    aws_access_key_id=os.environ.get("WL_CHECK_KEY") or None,
    aws_secret_access_key=os.environ.get("WL_CHECK_SECRET") or None,
    region_name=os.environ.get("WL_CHECK_REGION") or "eu-west-1",
)
sts = session.client("sts")
ident = sts.get_caller_identity()
print(f"identite source : {ident['Arn']}")
role = os.environ.get("WL_CHECK_ROLE_ARN")
if role:
    kwargs = {"RoleArn": role, "RoleSessionName": "wasteless-install-check"}
    if os.environ.get("WL_CHECK_EXTERNAL_ID"):
        kwargs["ExternalId"] = os.environ["WL_CHECK_EXTERNAL_ID"]
    sts.assume_role(**kwargs)
    print(f"assume-role : OK ({role})")
PYCHECK
        ); then
            print_step "Connexion AWS verifiee"
            echo "$AWS_VALIDATION_OUTPUT" | sed 's/^/    /'
            break
        else
            print_error "Validation AWS echouee :"
            echo "$AWS_VALIDATION_OUTPUT" | tail -n 2 | sed 's/^/    /'
            read -p "Ressaisir les valeurs ? (o/N): " AWS_RETRY
            if [[ ! "$AWS_RETRY" =~ ^[OoYy]$ ]]; then
                print_warning "Valeurs enregistrees telles quelles — corrigez-les via la page /setup de l'interface (ou dans .env et ui/.env)"
                break
            fi
            AWS_ROLE_ARN=""; AWS_WRITE_ROLE_ARN=""; AWS_EXTERNAL_ID=""
            AWS_ACCESS_KEY_ID=""; AWS_SECRET_ACCESS_KEY=""
            AWS_CONFIGURED=0
        fi
    done

    # Insights IA (optionnel, opt-in explicite — saute en mode quiet)
    LLM_MODEL=""
    LLM_KEY_VAR=""
    LLM_API_KEY=""
    if [ $VERBOSE -eq 1 ]; then
        echo ""
        print_info "Insights IA (optionnel)"
        echo "  WasteLess peut expliquer chaque recommandation via un LLM."
        echo "  Les metadonnees de vos ressources AWS seront envoyees au provider choisi."
        echo "  1) DeepSeek   2) Claude (Anthropic)   3) OpenAI   4) Ollama (local, sans cle)"
        read -p "Choisissez un provider [Entree = passer]: " LLM_CHOICE

        case "$LLM_CHOICE" in
            1) LLM_MODEL="deepseek/deepseek-chat";  LLM_KEY_VAR="DEEPSEEK_API_KEY" ;;
            2) LLM_MODEL="anthropic/claude-haiku-4-5-20251001"; LLM_KEY_VAR="ANTHROPIC_API_KEY" ;;
            3) LLM_MODEL="openai/gpt-4o-mini";      LLM_KEY_VAR="OPENAI_API_KEY" ;;
            4) read -p "Modele Ollama [llama3.1]: " OLLAMA_MODEL
               LLM_MODEL="ollama/${OLLAMA_MODEL:-llama3.1}" ;;
            "") print_step "Insights IA ignores (activables plus tard: voir .env.template)" ;;
            *)  print_warning "Choix invalide — insights IA ignores (activables plus tard: voir .env.template)" ;;
        esac

        if [ -n "$LLM_KEY_VAR" ]; then
            read -sp "Cle API ($LLM_KEY_VAR): " LLM_API_KEY
            echo ""
        fi
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

    if [ -n "$AWS_ROLE_ARN" ]; then
        echo "AWS_ROLE_ARN=$AWS_ROLE_ARN" >> .env
        [ -n "$AWS_WRITE_ROLE_ARN" ] && echo "AWS_WRITE_ROLE_ARN=$AWS_WRITE_ROLE_ARN" >> .env
        [ -n "$AWS_EXTERNAL_ID" ] && echo "AWS_EXTERNAL_ID=$AWS_EXTERNAL_ID" >> .env
    fi

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

    if [ -n "$LLM_MODEL" ]; then
        cat >> .env << EOF

# AI insights (litellm — see .env.template for other providers)
WASTELESS_LLM_MODEL=$LLM_MODEL
EOF
        if [ -n "$LLM_API_KEY" ]; then
            echo "$LLM_KEY_VAR=$LLM_API_KEY" >> .env
        fi
    fi

    chmod 600 .env
    print_step "Fichier .env cree et securise"

    # Installation + test de la config LLM (fail-soft: n'interrompt jamais l'installation)
    if [ -n "$LLM_MODEL" ]; then
        print_info "Installation de litellm..."
        install_llm_ok=1
        if [ $USE_UV -eq 1 ]; then
            silence uv pip install --python venv/bin/python3 litellm $PIP_OPT || install_llm_ok=0
        else
            silence venv/bin/pip install litellm $PIP_OPT || install_llm_ok=0
        fi
        if [ $install_llm_ok -eq 0 ]; then
            print_warning "Installation de litellm echouee — installez-le manuellement: pip install litellm"
        else
            print_info "Test de la configuration LLM ($LLM_MODEL)..."
            if venv/bin/python3 - << 'PYEOF'
import os, sys
from dotenv import load_dotenv
load_dotenv('.env')
try:
    import litellm
    litellm.completion(
        model=os.getenv('WASTELESS_LLM_MODEL'),
        messages=[{'role': 'user', 'content': 'ping'}],
        max_tokens=5, timeout=20)
except Exception as e:
    print(f"  {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
            then
                print_step "Insights IA configures et testes"
            else
                print_warning "Le test LLM a echoue — verifiez la cle dans .env"
                print_info "L'installation continue: les recommandations n'auront simplement pas d'insight IA"
            fi
        fi
    fi
fi

# =============================================================================
# DEMARRAGE DE LA BASE DE DONNEES
# =============================================================================
print_header "4/7 - Demarrage de la base de donnees"

# Charger la config DB depuis .env sans `source` (parsing sur, pas d'execution)
load_env_file ".env" || exit 1

# Signaler un eventuel conflit de port avant de tenter le demarrage
check_port_conflict

print_info "Demarrage de PostgreSQL via Docker Compose..."

# Un `compose up` peut echouer sur un /var/lib/containerd partiellement
# efface (voir check_docker_store) : le repertoire des snapshots manque et
# seul un redemarrage du demon le recree. Detecter cette signature et
# reessayer une fois plutot que de laisser l'erreur brute a l'utilisateur.
start_postgres_container() {
    local out
    if out="$(compose up -d postgres 2>&1)"; then
        return 0
    fi
    if echo "$out" | grep -q 'failed to create prepare snapshot dir'; then
        print_warning "Repertoire de travail containerd manquant (store partiellement efface)"
        if restart_docker_daemon && out="$(compose up -d postgres 2>&1)"; then
            return 0
        fi
    fi
    print_error "Echec du demarrage de PostgreSQL :"
    echo "$out" >&2
    return 1
}

if docker ps --format '{{.Names}}' | grep -q '^wasteless-postgres$'; then
    print_warning "PostgreSQL deja en cours d'execution"
elif docker ps -a --format '{{.Names}}' | grep -q '^wasteless-postgres$'; then
    print_warning "Conteneur wasteless-postgres existant detecte (arrete) — tentative de redemarrage..."
    if ! silence docker start wasteless-postgres; then
        print_warning "Redemarrage echoue (volumes invalides) — suppression et recreation du conteneur..."
        silence docker rm wasteless-postgres
        start_postgres_container || exit 1
        print_step "PostgreSQL recree et demarre"
    else
        print_step "PostgreSQL redemarre"
    fi
else
    start_postgres_container || exit 1
    print_step "PostgreSQL demarre"
fi

# Attente robuste (120s, sortie anticipee si crash, dump diagnostic si echec)
wait_for_postgres 120 || exit 1

# =============================================================================
# INITIALISATION DU SCHEMA
# =============================================================================
print_header "5/7 - Initialisation du schema de base de donnees"

# Compter les migrations avant de commencer
MIGRATION_COUNT=0
for migration in sql/ec2_metrics.sql sql/migrations/*.sql; do
    [ -f "$migration" ] && MIGRATION_COUNT=$((MIGRATION_COUNT + 1))
done

print_info "Application de $MIGRATION_COUNT migration(s)..."

# ON_ERROR_STOP=1 : une migration qui echoue interrompt l'install au lieu de
# passer inapercue (l'ancien `|| true` masquait tout). Les migrations sont
# idempotentes (CREATE ... IF NOT EXISTS / OR REPLACE, gardes DO $$), donc
# une reexecution ne produit pas d'erreur.
MIGRATION_NUM=0
MIGRATION_FAILED=0
for migration in sql/ec2_metrics.sql sql/migrations/*.sql; do
    if [ -f "$migration" ]; then
        MIGRATION_NUM=$((MIGRATION_NUM + 1))
        MIGRATION_NAME=$(basename "$migration")
        echo -ne "  ${BLUE}[${MIGRATION_NUM}/${MIGRATION_COUNT}]${NC} ${MIGRATION_NAME}..."
        START_TIME=$(date +%s)
        if [ $VERBOSE -eq 1 ]; then
            echo ""
            if ! docker exec -i wasteless-postgres psql -v ON_ERROR_STOP=1 -U "${DB_USER:-wasteless}" -d "${DB_NAME:-wasteless}" < "$migration"; then
                MIGRATION_FAILED=1; echo -e " ${RED}ECHEC${NC}"; break
            fi
        else
            if ! docker exec -i wasteless-postgres psql -v ON_ERROR_STOP=1 -U "${DB_USER:-wasteless}" -d "${DB_NAME:-wasteless}" < "$migration" &>/dev/null; then
                MIGRATION_FAILED=1; echo -e " ${RED}ECHEC${NC}"; break
            fi
        fi
        ELAPSED=$(( $(date +%s) - START_TIME ))
        echo -e " ${GREEN}OK${NC} (${ELAPSED}s)"
    fi
done

if [ $MIGRATION_FAILED -eq 1 ]; then
    print_error "Migration '$MIGRATION_NAME' echouee — installation interrompue"
    print_info "Relancez en mode verbose pour voir l'erreur SQL: ./install.sh (sans -q)"
    exit 1
fi

print_step "Schema de base de donnees initialise"

# =============================================================================
# INSTALLATION DE L'INTERFACE WEB
# =============================================================================
print_header "6/7 - Installation de l'interface web"

# Environnement virtuel UI
if [ -d "ui/venv" ]; then
    if ! ui/venv/bin/python3 -c "import sys" &> /dev/null; then
        print_warning "Environnement virtuel UI corrompu — recreation automatique"
        rm -rf ui/venv
        create_venv ui/venv
        print_step "Environnement virtuel UI recree"
    else
        print_step "Environnement virtuel UI deja present"
    fi
else
    create_venv ui/venv
    print_step "Environnement virtuel UI cree"
fi

print_verbose "installation des dependances UI (ui/requirements.lock)"
# Idem cote UI : lock epingle. Regenerer :
#   uv pip compile ui/requirements.txt --universal --output-file ui/requirements.lock
install_deps ui/venv ui/requirements.lock

# Backend (src/core, src/remediators, ...) installe en editable dans le venv
# UI: ui/utils/remediator.py fait `from remediators.ec2_remediator import ...`
# directement, sans sys.path.insert() pointant vers la racine du repo.
print_verbose "installation du backend en editable dans ui/venv (pyproject.toml)"
if [ $USE_UV -eq 1 ]; then
    silence uv pip install --python ui/venv/bin/python3 -e .
else
    silence ui/venv/bin/pip install -e . $PIP_OPT
fi

# litellm aussi dans le venv UI: la page Reports genere le resume IA
# depuis le processus UI (fail-soft, comme pour le venv racine)
if grep -q '^WASTELESS_LLM_MODEL=' .env 2>/dev/null; then
    if [ $USE_UV -eq 1 ]; then
        silence uv pip install --python ui/venv/bin/python3 litellm $PIP_OPT \
            || print_warning "litellm non installe dans ui/venv — le resume IA de la page Reports sera masque"
    else
        silence ui/venv/bin/pip install litellm $PIP_OPT \
            || print_warning "litellm non installe dans ui/venv — le resume IA de la page Reports sera masque"
    fi
fi
print_step "Dependances UI installees"

# Fichier ui/.env — toujours mis a jour pour reflechir le chemin courant
CURRENT_PATH="$(pwd)"
if [ -f "ui/.env" ]; then
    UPDATED=0
    # Mettre a jour WASTELESS_BACKEND_PATH si le projet a ete deplace
    STORED_PATH=$(grep '^WASTELESS_BACKEND_PATH=' ui/.env | cut -d= -f2)
    if [ "$STORED_PATH" != "$CURRENT_PATH" ]; then
        sed_inplace "s|^WASTELESS_BACKEND_PATH=.*|WASTELESS_BACKEND_PATH=$CURRENT_PATH|" ui/.env
        UPDATED=1
    fi
    # Synchroniser le mot de passe DB depuis le root .env
    STORED_PW=$(grep '^DB_PASSWORD=' ui/.env | cut -d= -f2)
    if [ "$STORED_PW" != "$DB_PASSWORD" ]; then
        sed_inplace "s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASSWORD|" ui/.env
        UPDATED=1
    fi
    # Account ID toujours reflete, meme quand la connexion AWS est reportee :
    # la page /setup s'en sert pour pre-remplir les ARNs des roles et le
    # lien quick-create CloudFormation.
    if [ -n "${AWS_ACCOUNT_ID:-}" ]; then
        set_env_kv ui/.env AWS_ACCOUNT_ID "$AWS_ACCOUNT_ID"
        UPDATED=1
    fi
    # Si l'utilisateur vient de (re)configurer AWS a l'etape 3, refleter les
    # valeurs dans ui/.env existant — le miroir manuel etait un piege.
    if [ -z "${SKIP_ENV_CONFIG:-}" ] && [ "${AWS_CONFIGURED:-0}" -eq 1 ]; then
        set_env_kv ui/.env AWS_REGION "$AWS_REGION"
        [ -n "$AWS_ROLE_ARN" ] && set_env_kv ui/.env AWS_ROLE_ARN "$AWS_ROLE_ARN"
        [ -n "$AWS_WRITE_ROLE_ARN" ] && set_env_kv ui/.env AWS_WRITE_ROLE_ARN "$AWS_WRITE_ROLE_ARN"
        [ -n "$AWS_EXTERNAL_ID" ] && set_env_kv ui/.env AWS_EXTERNAL_ID "$AWS_EXTERNAL_ID"
        [ -n "$AWS_ACCESS_KEY_ID" ] && set_env_kv ui/.env AWS_ACCESS_KEY_ID "$AWS_ACCESS_KEY_ID"
        [ -n "$AWS_SECRET_ACCESS_KEY" ] && set_env_kv ui/.env AWS_SECRET_ACCESS_KEY "$AWS_SECRET_ACCESS_KEY"
        UPDATED=1
    fi
    if [ $UPDATED -eq 1 ]; then
        print_step "ui/.env synchronise avec la configuration courante"
    else
        print_step "Configuration UI existante conservee"
    fi
else
    cat > ui/.env << UIENV
# WasteLess UI Configuration - Generated: $(date)
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-wasteless}
DB_USER=${DB_USER:-wasteless}
DB_PASSWORD=${DB_PASSWORD}
WASTELESS_BACKEND_PATH=$CURRENT_PATH
STREAMLIT_SERVER_PORT=8888
STREAMLIT_SERVER_ADDRESS=localhost
# Adresse d'ecoute de l'UI. 127.0.0.1 par defaut : l'API n'a pas
# d'authentification et ses endpoints POST executent de vraies actions AWS.
# Ne passer a 0.0.0.0 que derriere un reverse proxy authentifie.
WASTELESS_HOST=127.0.0.1
LOG_LEVEL=INFO
AWS_REGION=${AWS_REGION:-eu-west-1}
UIENV
    # Account ID meme sans connexion configuree : /setup pre-remplit les ARNs
    # des roles et le lien quick-create CloudFormation avec.
    [ -n "${AWS_ACCOUNT_ID:-}" ] && echo "AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID" >> ui/.env
    if [ -n "${AWS_ROLE_ARN:-}" ]; then
        echo "AWS_ROLE_ARN=$AWS_ROLE_ARN" >> ui/.env
        [ -n "${AWS_WRITE_ROLE_ARN:-}" ] && echo "AWS_WRITE_ROLE_ARN=$AWS_WRITE_ROLE_ARN" >> ui/.env
        [ -n "${AWS_EXTERNAL_ID:-}" ] && echo "AWS_EXTERNAL_ID=$AWS_EXTERNAL_ID" >> ui/.env
    fi
    # Les cles statiques aussi : l'UI interroge AWS en direct (page
    # cloud-resources, sync) et ne lit que ui/.env — sans ce miroir, une
    # installation "cles directes" donnait une UI sans credentials.
    if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
        echo "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" >> ui/.env
        echo "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY" >> ui/.env
    fi
    chmod 600 ui/.env
    print_step "Configuration UI creee (ui/.env)"
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
        sed_inplace "s|alias wasteless=.*|$ALIAS_LINE|" "$SHELL_RC"
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
if silence python3 -c "
from src.core.database import health_check
import sys
sys.exit(0 if health_check() else 1)
"; then
    print_step "Connexion base de donnees OK"
else
    print_warning "Test de connexion echoue (normal si premiere installation)"
fi

# Test des modules. Deux verifications distinctes, volontairement separees :
# un assert "enabled == False" melange a l'import a deja fait passer un
# reglage legitime (auto-remediation activee via Settings, install.sh
# relance en mise a jour) pour une erreur de chargement des modules, avec
# traceback brut chez un client. L'etat de l'auto-remediation est un choix
# de l'utilisateur : l'installation l'annonce, elle ne l'exige pas.
print_info "Verification des modules..."
if MODULES_CHECK_OUTPUT=$(python3 -c "
from src.core.config import RemediationConfig
config = RemediationConfig.from_yaml('config/remediation.yaml')
print('enabled' if config.enabled else 'disabled')
" 2>&1); then
    print_step "Modules Python OK"
    if [ "$MODULES_CHECK_OUTPUT" = "disabled" ]; then
        print_step "Auto-remediation desactivee (securite)"
    else
        print_warning "Auto-remediation ACTIVE (heritee de votre configuration existante)"
        print_info "Desactivable dans Settings ou config/remediation.yaml (auto_remediation.enabled)"
    fi
else
    print_error "Erreur lors du chargement des modules :"
    echo "$MODULES_CHECK_OUTPUT" | tail -n 3 | sed 's/^/    /'
fi

# Test credentials AWS. load_dotenv est indispensable : boto3 ne lit pas
# .env tout seul, et sans ca le test annoncait "invalides" meme quand les
# cles venaient d'etre ecrites dans .env (seul ~/.aws etait vu).
# Toujours &>/dev/null (pas `silence`) : cette sonde echoue normalement
# quand AWS n'est pas encore configure, et son traceback NoCredentialsError
# ne doit jamais s'afficher : le diagnostic passe par les messages ci-dessous.
print_info "Verification des credentials AWS..."
AWS_CHECK_OK=0
if python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
import boto3
sts = boto3.client('sts', region_name=os.getenv('AWS_REGION') or 'eu-west-1')
sts.get_caller_identity()
" &>/dev/null; then
    AWS_CHECK_OK=1
    print_step "Credentials AWS valides"
elif grep -qE '^(AWS_ACCESS_KEY_ID|AWS_ROLE_ARN)=' .env 2>/dev/null; then
    print_warning "Credentials AWS invalides ou inaccessibles"
    print_info "Corrigez-les via http://localhost:8888/setup (ou dans .env et ui/.env)"
else
    print_warning "AWS non configure — le dashboard restera vide"
    print_info "Connexion guidee: http://localhost:8888/setup ou docs/CTO_QUICKSTART.md (10 min)"
fi

# Tests unitaires
print_info "Execution des tests unitaires..."
PYTEST_OPT=$([ $VERBOSE -eq 0 ] && echo "-q" || echo "-v")
if silence ./venv/bin/pytest tests/unit/test_validation.py $PYTEST_OPT; then
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
# Etat de la connexion AWS en tete du resume : c'est LE prerequis pour que
# le produit montre quelque chose, il ne doit pas se perdre dans le defilement.
if [ "$AWS_CHECK_OK" -eq 1 ]; then
    echo -e "  ${GREEN}AWS : connecte et verifie${NC}"
else
    echo -e "  ${YELLOW}AWS : non connecte — le dashboard restera vide tant que ce n'est pas fait.${NC}"
    echo "        Le navigateur va s'ouvrir sur le guide de connexion (http://localhost:8888/setup)"
    echo "        Guide detaille : docs/CTO_QUICKSTART.md"
fi
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
echo "  - /reports           Rapports d'activite + resume IA"
echo "  - /logs              Logs applicatifs (debug)"
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

# Collecte automatique au niveau OS (survit au reboot). launchd (macOS) /
# systemd user timer (Linux) / cron (fallback). Sans ca, la collecte ne tourne
# que tant que l'UI est lancee via `wasteless start` (loop en process).
# Coupe avec --no-schedule.
if [ "$SETUP_SCHEDULE" -eq 1 ]; then
    print_header "Collecte automatique"
    if confirm_system_change "Installer la collecte automatique toutes les 5 min (survit au reboot) ?"; then
        ./wasteless.sh schedule || print_warning "Installation du scheduler echouee — activez-la plus tard: wasteless schedule"
    else
        print_info "Collecte automatique non installee. Activez-la quand vous voulez: wasteless schedule"
    fi
fi

# Demarrage automatique — pas besoin de sourcer le shell
print_header "Demarrage de l'interface"
./wasteless.sh || print_warning "Demarrage automatique echoue. Lancez manuellement: ./wasteless.sh"
