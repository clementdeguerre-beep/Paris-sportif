#!/usr/bin/env bash
# Avant de parier : récupère les relevés de GitHub Actions, les importe, puis lance le scan.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull -q --ff-only
.venv/bin/tennisvalue import-odds
.venv/bin/tennisvalue scan
