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
cd "$SCRIPT_DIR" || exit 1

# Connexion AWS lue AVANT la suppression des .env (etape 3) : l'etape 6 en a
# besoin pour retrouver la stack d'onboarding. Lecture par grep, jamais par
# `source` (un mot de passe contenant $ ou ` executerait du shell).
get_env_var() { grep -E "^$1=" "$SCRIPT_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2-; }
ENV_AWS_ROLE_ARN="$(get_env_var AWS_ROLE_ARN)"
ENV_AWS_REGION="${AWS_REGION:-$(get_env_var AWS_REGION)}"
ENV_AWS_REGION="${ENV_AWS_REGION:-eu-west-1}"

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
print_header "1/6 - Arret de WasteLess"

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

# Filet de securite: instances uvicorn lancees sans fichier PID (demarrage manuel,
# ancienne version des scripts) survivent au bloc precedent — on tue par port.
UI_PORT="${WASTELESS_PORT:-8888}"
SURVIVORS=$(pgrep -f "uvicorn main:app.*--port $UI_PORT" 2>/dev/null || true)
if [ -n "$SURVIVORS" ]; then
    kill $SURVIVORS 2>/dev/null || true
    sleep 1
    pkill -9 -f "uvicorn main:app.*--port $UI_PORT" 2>/dev/null || true
    print_step "Instance(s) uvicorn survivante(s) arretee(s) (PID: $(echo $SURVIVORS | tr '\n' ' '))"
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
print_header "2/6 - Arret des services Docker"

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
print_header "3/6 - Suppression des fichiers locaux"

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
# 4. SUPPRIMER LES TACHES AUTOMATIQUES (launchd / systemd / cron)
# =============================================================================
print_header "4/6 - Suppression des taches automatiques"

# wasteless.sh unschedule retire le backend reellement utilise sur cette
# plateforme (LaunchAgent macOS, timer systemd ou crontab) — le nettoyage
# crontab ci-dessous ne couvre que les anciens marqueurs et ne suffit pas
# seul : sans cet appel, un LaunchAgent/timer survivrait a la
# desinstallation et relancerait la collecte toutes les 5 minutes.
if [ -x "$SCRIPT_DIR/wasteless.sh" ]; then
    "$SCRIPT_DIR/wasteless.sh" unschedule || print_warning "wasteless.sh unschedule a echoue — verifiez avec: wasteless status"
fi

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
# 5. SUPPRIMER LA COMMANDE 'wasteless'
# =============================================================================
print_header "5/6 - Suppression de la commande 'wasteless'"

SHELL_RCS=("$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc")
ALIAS_REMOVED=0

# Symlink installe par install.sh : ne le supprimer que s'il pointe vers cette
# installation (marqueur ~/.config/wasteless/root) ou vers ce repo.
ROOT_MARKER="$HOME/.config/wasteless/root"
if [ -L "$HOME/.local/bin/wasteless" ]; then
    TARGET="$(readlink "$HOME/.local/bin/wasteless" 2>/dev/null || true)"
    MARKER_ROOT="$(cat "$ROOT_MARKER" 2>/dev/null || true)"
    if [[ "$TARGET" == "$SCRIPT_DIR/wasteless.sh" || ( -n "$MARKER_ROOT" && "$TARGET" == "$MARKER_ROOT/wasteless.sh" ) ]]; then
        rm -f "$HOME/.local/bin/wasteless"
        print_step "Symlink ~/.local/bin/wasteless supprime"
        ALIAS_REMOVED=1
    else
        print_skip "Symlink ~/.local/bin/wasteless conserve (ne correspond pas a l'installation WasteLess)"
    fi
fi
rm -rf "$HOME/.config/wasteless"

# Retirer un ancien alias (installs precedents) + le commentaire de section, de
# facon portable (grep -v : pas de 'sed -i' dont la syntaxe differe GNU/BSD). On
# laisse volontairement un eventuel 'export PATH=...local/bin' : benin et
# souvent partage avec d'autres outils.
for SHELL_RC in "${SHELL_RCS[@]}"; do
    if [ -f "$SHELL_RC" ] && grep -qE "alias wasteless=|^# WasteLess CLI$" "$SHELL_RC" 2>/dev/null; then
        tmp="$(mktemp)"
        grep -vE '^# WasteLess CLI$|alias wasteless=' "$SHELL_RC" > "$tmp" && cat "$tmp" > "$SHELL_RC"
        rm -f "$tmp"
        print_step "Reference 'wasteless' retiree de $SHELL_RC"
        ALIAS_REMOVED=1
    fi
done

if [ "$ALIAS_REMOVED" -eq 0 ]; then
    print_skip "Aucune commande 'wasteless' trouvee (symlink / alias)"
fi

# =============================================================================
# 6. ROLES IAM CREES DANS AWS (stack d'onboarding)
# =============================================================================
print_header "6/6 - Roles IAM wasteless dans AWS"

# Les seules ressources AWS creees par wasteless pour lui-meme sont les roles
# IAM d'onboarding (wasteless-readonly / wasteless-remediation), poses par la
# stack CloudFormation ou le module Terraform. Leur suppression exige des
# droits IAM que les roles wasteless n'ont pas : on passe par les credentials
# ambiants (aws configure). Un echec ici ne fait jamais echouer la
# desinstallation locale.
STACK_NAME="${WASTELESS_ONBOARDING_STACK:-wasteless-onboarding}"
AWS_ROLES_DELETED=0

print_manual_teardown() {
    echo "    Console : CloudFormation -> stack '$STACK_NAME' -> Delete"
    echo "    Ou      : terraform destroy dans votre module onboarding/terraform"
}

if [ -z "$ENV_AWS_ROLE_ARN" ]; then
    print_skip "Aucun role IAM wasteless configure (AWS_ROLE_ARN absent du .env) — rien a supprimer cote AWS"
else
    echo "  WasteLess utilisait ce role IAM dans votre compte AWS :"
    echo "    - $ENV_AWS_ROLE_ARN"
    echo "    (+ le role remediation s'il avait ete cree)"
    echo ""
    read -rp "  Supprimer ces roles maintenant (stack '$STACK_NAME', region $ENV_AWS_REGION) ? [o/N]: " DELETE_AWS_ROLES
    case "$DELETE_AWS_ROLES" in
        [oOyY]*)
            if ! command -v aws &>/dev/null; then
                print_warning "AWS CLI absent — supprimez les roles manuellement :"
                print_manual_teardown
            elif aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$ENV_AWS_REGION" &>/dev/null; then
                if aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$ENV_AWS_REGION"; then
                    print_info "Suppression lancee, attente de la confirmation (moins d'une minute en general)..."
                    if aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$ENV_AWS_REGION" 2>/dev/null; then
                        print_step "Stack '$STACK_NAME' supprimee — les roles IAM wasteless n'existent plus"
                        AWS_ROLES_DELETED=1
                    else
                        print_warning "Suppression non confirmee — verifiez l'etat de la stack dans la console CloudFormation"
                    fi
                else
                    print_warning "Echec de la suppression (droits IAM insuffisants ?) — faites-le avec un compte admin :"
                    print_manual_teardown
                fi
            else
                print_warning "Stack '$STACK_NAME' introuvable dans la region $ENV_AWS_REGION"
                echo "    - Onboarding fait via Terraform : terraform destroy dans onboarding/terraform"
                echo "    - Autre nom ou autre region     : relancez avec"
                echo "      WASTELESS_ONBOARDING_STACK=<nom> AWS_REGION=<region> ./uninstall.sh"
            fi
            ;;
        *)
            print_skip "Roles IAM conserves. Pour les supprimer plus tard :"
            print_manual_teardown
            ;;
    esac
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
echo "  - Taches automatiques (launchd / systemd / cron)"
echo "  - Alias 'wasteless' du shell"
if [ "$AWS_ROLES_DELETED" -eq 1 ]; then
    echo "  - Roles IAM wasteless dans AWS (stack '$STACK_NAME')"
fi
echo ""

if [ "$FULL_UNINSTALL" -eq 1 ]; then
    echo -e "${RED}${BOLD}Donnees supprimees:${NC}"
    echo "  - Volumes Docker (base de donnees PostgreSQL)"
    echo ""
else
    echo -e "${YELLOW}${BOLD}Conserve:${NC}"
    echo "  - Volumes Docker (base de donnees PostgreSQL)"
    echo "  - Code source du projet"
    if [ "$AWS_ROLES_DELETED" -eq 0 ] && [ -n "$ENV_AWS_ROLE_ARN" ]; then
        echo "  - Roles IAM wasteless dans AWS (voir etape 6 pour les supprimer)"
    fi
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
