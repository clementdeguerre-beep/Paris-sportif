#!/usr/bin/env bash
# Ouvre l'application Tennis Value dans le navigateur, en la démarrant si besoin.
# C'est la commande de l'icône « Tennis Value » du lanceur (créée par install_chromebook.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
URL="http://localhost:8501"

if ! curl -fsS "$URL/_stcore/health" >/dev/null 2>&1; then
  mkdir -p logs
  setsid nohup .venv/bin/python -m streamlit run dashboard/app.py >> logs/app.log 2>&1 < /dev/null &
  for _ in $(seq 60); do
    curl -fsS "$URL/_stcore/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi
xdg-open "$URL" >/dev/null 2>&1 || echo "Ouvrir $URL dans le navigateur."
