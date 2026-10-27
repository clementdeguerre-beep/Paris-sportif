#!/usr/bin/env bash
# Routine planifiée (cron) : relevé des cotes puis scan. Journal dans logs/routine.log.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
{
  echo "=== $(date '+%Y-%m-%d %H:%M') ==="
  .venv/bin/tennisvalue fetch-odds
  .venv/bin/tennisvalue scan
} >> logs/routine.log 2>&1
