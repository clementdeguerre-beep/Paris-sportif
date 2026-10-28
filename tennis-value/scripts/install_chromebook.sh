#!/usr/bin/env bash
# Installation complète sur Chromebook (environnement Linux) ou Debian/Ubuntu :
#   bash <(curl -fsSL https://raw.githubusercontent.com/clementdeguerre-beep/Paris-sportif/claude/zealous-sagan-qzgjyg/tennis-value/scripts/install_chromebook.sh)
# Installe le projet dans ~/Paris-sportif, lance build, ajoute l'icône Tennis Value au
# lanceur, enregistre la clé The Odds API comme secret GitHub (ODDS_API_KEY), lance un
# premier relevé GitHub Actions puis ouvre l'application.
# Relançable sans risque : les étapes déjà faites sont sautées.
set -euo pipefail

REPO=clementdeguerre-beep/Paris-sportif
BRANCH=claude/zealous-sagan-qzgjyg
DEST="${DEST:-$HOME/Paris-sportif}"

command -v apt-get >/dev/null || { echo "Ce script demande Debian ou Ubuntu (apt)."; exit 1; }

echo "== 1/7 Paquets système"
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

echo "== 2/7 Projet dans $DEST"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull -q --ff-only
else
  git clone -q -b "$BRANCH" "https://github.com/$REPO" "$DEST"
fi
cd "$DEST/tennis-value"

echo "== 3/7 Environnement Python (quelques minutes)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "== 4/7 Build (1 à 3 minutes)"
mkdir -p reports
if ! .venv/bin/tennisvalue build > reports/build.log 2>&1; then
  tail -20 reports/build.log
  exit 1
fi
tail -1 reports/build.log

echo "== 5/7 Icône Tennis Value dans le lanceur"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/tennis-value.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Tennis Value
Comment=Paris tennis : value bets, mises conseillées, suivi de bankroll
Exec=$DEST/tennis-value/scripts/lancer.sh
Icon=$DEST/tennis-value/src/tennisvalue/web/static/icone.png
Terminal=false
Categories=Utility;
DESKTOP

echo "== 6/7 Connexion à GitHub et clé The Odds API"
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
if gh secret list --repo "$REPO" | grep -q '^ODDS_API_KEY'; then
  echo "Secret ODDS_API_KEY déjà enregistré."
  NEW_KEY=0
else
  echo "Colle ta clé The Odds API (Ctrl+Maj+V) puis Entrée ; elle ne s'affiche pas :"
  gh secret set ODDS_API_KEY --repo "$REPO"
  NEW_KEY=1
fi

echo "== 7/7 Premier relevé sur GitHub Actions"
if [ "$NEW_KEY" = 0 ]; then
  echo "Déjà fait lors de l'installation (relancer un relevé coûte des crédits)."
else
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
  .venv/bin/tennisvalue import-polymarket
fi

echo
echo "Terminé. Les relevés tournent sur GitHub vers 8 h 30, 11 h 30, 14 h 30, 17 h 30 et 20 h 30 (une heure plus tard en été)."
echo "Pour ouvrir l'application : icône « Tennis Value » dans le lanceur (dossier Applications Linux)."
"$DEST/tennis-value/scripts/lancer.sh"
