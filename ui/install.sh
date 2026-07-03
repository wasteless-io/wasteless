#!/bin/bash
#
# Ce script redirige vers l'installation complète à la racine du projet.
# Lancez install.sh depuis la racine : ../install.sh
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "Installation complète disponible depuis la racine du projet."
echo "Lancement de $ROOT_DIR/install.sh ..."
echo ""

exec "$ROOT_DIR/install.sh" "$@"
