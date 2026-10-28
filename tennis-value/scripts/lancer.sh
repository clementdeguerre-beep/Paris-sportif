#!/usr/bin/env bash
# Ouvre l'application Tennis Value dans le navigateur, en la démarrant si besoin.
# C'est la commande de l'icône « Tennis Value » du lanceur (créée par install_chromebook.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
URL="http://localhost:8501"

running() { curl -fsS "$URL/api/health" 2>/dev/null | grep -q '"ok"'; }
if ! running; then
  mkdir -p logs
  setsid nohup .venv/bin/tennisvalue dashboard >> logs/app.log 2>&1 < /dev/null &
  for _ in $(seq 60); do
    running && break
    sleep 1
  done
fi
xdg-open "$URL" >/dev/null 2>&1 || echo "Ouvrir $URL dans le navigateur."
