#!/usr/bin/env bash
# =============================================================================
# github_users.sh — Gestion des utilisateurs GitHub pour wasteless
# =============================================================================
# Prérequis : GitHub CLI (gh) authentifié  →  brew install gh && gh auth login
# Usage     : ./scripts/github_users.sh <commande> [options]
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_OWNER="${GITHUB_OWNER:-wastelessio}"
REPO_NAME="${GITHUB_REPO:-wasteless}"
REPO="${REPO_OWNER}/${REPO_NAME}"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

require_gh() {
  command -v gh &>/dev/null || die "GitHub CLI (gh) non installé. Installez-le : brew install gh"
  gh auth status &>/dev/null   || die "Non authentifié. Lancez : gh auth login"
}

confirm() {
  local msg="$1"
  read -rp "$(echo -e "${YELLOW}${msg} [y/N] ${RESET}")" ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------

cmd_list() {
  # Liste tous les collaborateurs et leur permission
  require_gh
  info "Collaborateurs du dépôt ${BOLD}${REPO}${RESET}"
  echo ""
  printf "%-30s %-12s %-10s\n" "LOGIN" "PERMISSION" "ROLE"
  printf "%-30s %-12s %-10s\n" "------------------------------" "------------" "----------"
  gh api "repos/${REPO}/collaborators" --paginate \
    --jq '.[] | [.login, .permissions | to_entries | map(select(.value==true)) | last | .key // "none", .role_name // "-"] | @tsv' \
  | while IFS=$'\t' read -r login perm role; do
      printf "%-30s %-12s %-10s\n" "$login" "$perm" "$role"
    done
  echo ""
}

cmd_invite() {
  # Invite un utilisateur comme collaborateur
  require_gh
  local username="${1:-}"
  local permission="${2:-push}"   # pull | push | maintain | triage | admin

  [[ -z "$username" ]] && die "Usage: $0 invite <username> [pull|push|maintain|triage|admin]"

  local valid_perms="pull push maintain triage admin"
  [[ " $valid_perms " == *" $permission "* ]] || die "Permission invalide. Valeurs: $valid_perms"

  info "Invitation de ${BOLD}${username}${RESET} avec la permission ${BOLD}${permission}${RESET}..."
  gh api --method PUT "repos/${REPO}/collaborators/${username}" \
    -f permission="$permission" &>/dev/null
  success "${username} invité avec succès (permission: ${permission})"
}

cmd_remove() {
  # Supprime un collaborateur
  require_gh
  local username="${1:-}"
  [[ -z "$username" ]] && die "Usage: $0 remove <username>"

  confirm "Supprimer ${username} du dépôt ${REPO} ?" || { info "Annulé."; exit 0; }

  gh api --method DELETE "repos/${REPO}/collaborators/${username}"
  success "${username} supprimé du dépôt."
}

cmd_permission() {
  # Affiche ou modifie la permission d'un collaborateur
  require_gh
  local username="${1:-}"
  local new_perm="${2:-}"

  [[ -z "$username" ]] && die "Usage: $0 permission <username> [nouvelle_permission]"

  if [[ -z "$new_perm" ]]; then
    info "Permission de ${username} :"
    gh api "repos/${REPO}/collaborators/${username}/permission" \
      --jq '"  role: \(.role_name // "none")  |  permission: \(.permission)"'
  else
    local valid_perms="pull push maintain triage admin"
    [[ " $valid_perms " == *" $new_perm "* ]] || die "Permission invalide. Valeurs: $valid_perms"
    gh api --method PUT "repos/${REPO}/collaborators/${username}" \
      -f permission="$new_perm" &>/dev/null
    success "Permission de ${username} mise à jour → ${new_perm}"
  fi
}

cmd_pending() {
  # Liste les invitations en attente
  require_gh
  info "Invitations en attente pour ${BOLD}${REPO}${RESET}"
  echo ""
  local count=0
  while IFS=$'\t' read -r id login perm created; do
    printf "  ID: %-8s  Login: %-20s  Permission: %-10s  Envoyée le: %s\n" \
      "$id" "$login" "$perm" "$created"
    ((count++))
  done < <(gh api "repos/${REPO}/invitations" --paginate \
    --jq '.[] | [.id | tostring, .invitee.login // "unknown", .permissions, .created_at] | @tsv')

  [[ $count -eq 0 ]] && info "Aucune invitation en attente."
  echo ""
}

cmd_cancel_invite() {
  # Annule une invitation en attente par ID ou login
  require_gh
  local target="${1:-}"
  [[ -z "$target" ]] && die "Usage: $0 cancel-invite <invitation_id | username>"

  # Si c'est un login, cherche l'ID correspondant
  if ! [[ "$target" =~ ^[0-9]+$ ]]; then
    local inv_id
    inv_id=$(gh api "repos/${REPO}/invitations" --paginate \
      --jq ".[] | select(.invitee.login == \"${target}\") | .id" | head -1)
    [[ -z "$inv_id" ]] && die "Aucune invitation en attente pour '${target}'"
    target="$inv_id"
  fi

  confirm "Annuler l'invitation #${target} ?" || { info "Annulé."; exit 0; }
  gh api --method DELETE "repos/${REPO}/invitations/${target}"
  success "Invitation #${target} annulée."
}

cmd_audit() {
  # Rapport d'audit : accès admin, comptes sans activité récente, etc.
  require_gh
  info "Rapport d'audit — ${BOLD}${REPO}${RESET}"
  echo ""

  echo -e "${BOLD}Collaborateurs avec accès ADMIN :${RESET}"
  local admins=0
  while IFS=$'\t' read -r login perm; do
    if [[ "$perm" == "admin" ]]; then
      echo "  - $login"
      ((admins++))
    fi
  done < <(gh api "repos/${REPO}/collaborators" --paginate \
    --jq '.[] | [.login, .permissions | to_entries | map(select(.value==true)) | last | .key // "none"] | @tsv')
  [[ $admins -eq 0 ]] && echo "  (aucun)"

  echo ""
  echo -e "${BOLD}Invitations en attente :${RESET}"
  local pending
  pending=$(gh api "repos/${REPO}/invitations" --paginate --jq 'length')
  echo "  ${pending} invitation(s) en attente"

  echo ""
  echo -e "${BOLD}Nombre total de collaborateurs :${RESET}"
  local total
  total=$(gh api "repos/${REPO}/collaborators" --paginate --jq 'length')
  echo "  ${total} collaborateur(s)"
  echo ""
}

cmd_help() {
  cat <<EOF

${BOLD}github_users.sh${RESET} — Gestion des utilisateurs GitHub
Dépôt cible : ${CYAN}${REPO}${RESET}

${BOLD}USAGE${RESET}
  ./scripts/github_users.sh <commande> [arguments]

${BOLD}COMMANDES${RESET}
  ${GREEN}list${RESET}                          Liste tous les collaborateurs
  ${GREEN}invite${RESET}   <user> [permission]  Invite un utilisateur (défaut: push)
  ${GREEN}remove${RESET}   <user>               Supprime un collaborateur
  ${GREEN}permission${RESET} <user> [perm]      Affiche ou modifie une permission
  ${GREEN}pending${RESET}                       Liste les invitations en attente
  ${GREEN}cancel-invite${RESET} <id|user>       Annule une invitation en attente
  ${GREEN}audit${RESET}                         Rapport d'audit des accès
  ${GREEN}help${RESET}                          Affiche cette aide

${BOLD}PERMISSIONS${RESET}
  pull · push · triage · maintain · admin

${BOLD}VARIABLES D'ENVIRONNEMENT${RESET}
  GITHUB_OWNER   Propriétaire du dépôt (défaut: wastelessio)
  GITHUB_REPO    Nom du dépôt          (défaut: wasteless)

${BOLD}EXEMPLES${RESET}
  ./scripts/github_users.sh list
  ./scripts/github_users.sh invite octocat push
  ./scripts/github_users.sh permission octocat admin
  ./scripts/github_users.sh remove octocat
  ./scripts/github_users.sh audit

EOF
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
main() {
  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    list)           cmd_list "$@" ;;
    invite)         cmd_invite "$@" ;;
    remove)         cmd_remove "$@" ;;
    permission)     cmd_permission "$@" ;;
    pending)        cmd_pending "$@" ;;
    cancel-invite)  cmd_cancel_invite "$@" ;;
    audit)          cmd_audit "$@" ;;
    help|--help|-h) cmd_help ;;
    *)              error "Commande inconnue : ${cmd}"; cmd_help; exit 1 ;;
  esac
}

main "$@"
