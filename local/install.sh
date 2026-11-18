#!/usr/bin/env bash
# Installe les scripts de déclenchement local dans ~/.local/bin.
# Ne modifie PAS la crontab : la ligne à ajouter (crontab -e) est dans local/crontab.txt.
# Prérequis : gh (connecté au compte propriétaire du repo) et jq.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/.local/bin" "$HOME/.local/state"
install -m 755 "$here/steamdeck-trigger" "$here/steamdeck-table" "$here/steamdeck-status" "$HOME/.local/bin/"
echo "Scripts installés dans ~/.local/bin."
echo "Ajoute cette ligne avec 'crontab -e' :"
cat "$here/crontab.txt"
