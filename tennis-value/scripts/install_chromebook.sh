#!/usr/bin/env bash
# Installation complète sur Chromebook (environnement Linux) ou Debian/Ubuntu :
#   bash <(curl -fsSL https://raw.githubusercontent.com/clementdeguerre-beep/Paris-sportif/claude/zealous-sagan-qzgjyg/tennis-value/scripts/install_chromebook.sh)
# Installe le projet dans ~/Paris-sportif, lance build, enregistre la clé The Odds
# API comme secret GitHub (ODDS_API_KEY) et lance un premier relevé GitHub Actions.
# Relançable sans risque : les étapes déjà faites sont sautées.
set -euo pipefail

REPO=clementdeguerre-beep/Paris-sportif
BRANCH=claude/zealous-sagan-qzgjyg
DEST="${DEST:-$HOME/Paris-sportif}"

command -v apt-get >/dev/null || { echo "Ce script demande Debian ou Ubuntu (apt)."; exit 1; }

echo "== 1/6 Paquets système"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv curl
if ! command -v gh >/dev/null; then
  # Dépôt officiel de GitHub CLI : https://github.com/cli/cli/blob/trunk/docs/install_linux.md
  sudo mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq gh
fi

echo "== 2/6 Projet dans $DEST"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull -q --ff-only
else
  git clone -q -b "$BRANCH" "https://github.com/$REPO" "$DEST"
fi
cd "$DEST/tennis-value"

echo "== 3/6 Environnement Python (quelques minutes)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "== 4/6 Build (1 à 3 minutes)"
mkdir -p reports
if ! .venv/bin/tennisvalue build > reports/build.log 2>&1; then
  tail -20 reports/build.log
  exit 1
fi
tail -1 reports/build.log

echo "== 5/6 Connexion à GitHub et clé The Odds API"
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
if gh secret list --repo "$REPO" | grep -q '^ODDS_API_KEY'; then
  echo "Secret ODDS_API_KEY déjà enregistré."
else
  echo "Colle ta clé The Odds API (Ctrl+Maj+V) puis Entrée ; elle ne s'affiche pas :"
  gh secret set ODDS_API_KEY --repo "$REPO"
fi

echo "== 6/6 Premier relevé sur GitHub Actions"
gh workflow run fetch-odds.yml --repo "$REPO" --ref "$BRANCH"
sleep 8
run_id=$(gh run list --repo "$REPO" --workflow fetch-odds.yml --event workflow_dispatch --limit 1 \
         --json databaseId -q '.[0].databaseId')
if ! gh run watch "$run_id" --repo "$REPO" --exit-status; then
  echo "Le relevé a échoué : voir https://github.com/$REPO/actions"
  echo "Clé erronée ? La remplacer avec : gh secret set ODDS_API_KEY --repo $REPO"
  exit 1
fi
git -C "$DEST" pull -q --ff-only
.venv/bin/tennisvalue import-odds

echo
echo "Terminé. Les relevés tournent sur GitHub à 8 h, 11 h, 14 h, 17 h et 20 h."
echo "Avant de parier : ~/Paris-sportif/tennis-value/scripts/sync.sh"
