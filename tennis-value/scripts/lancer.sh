#!/usr/bin/env bash
# Ouvre l'application Tennis Value dans le navigateur, en la (re)démarrant si besoin.
# C'est la commande de l'icône « Tennis Value » du lanceur (créée par install_chromebook.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
URL="http://localhost:8501"
VERSION=$(git log -1 --format=%h -- src pyproject.toml scripts 2>/dev/null || true)

running() { curl -fsS "$URL/api/health" 2>/dev/null | grep -q "\"version\":\"$VERSION\""; }
if ! running; then
  mkdir -p logs
  # Une version précédente tourne peut-être encore (dont l'ancienne interface Streamlit) :
  # on l'arrête, on attend que le port se libère, puis on met les dépendances à jour.
  pkill -f "streamlit run dashboard/app.py" 2>/dev/null || true
  pkill -f "tennisvalue dashboard" 2>/dev/null || true
  for _ in $(seq 10); do
    curl -fsS -o /dev/null "$URL" 2>/dev/null || break
    sleep 1
  done
  .venv/bin/pip install -q -e . >> logs/app.log 2>&1 || true
  setsid nohup .venv/bin/tennisvalue dashboard >> logs/app.log 2>&1 < /dev/null &
  for _ in $(seq 60); do
    running && break
    sleep 1
  done
fi
xdg-open "$URL" >/dev/null 2>&1 || echo "Ouvrir $URL dans le navigateur."
