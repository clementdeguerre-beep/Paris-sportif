# Tennis Value — MVP v0.1

Logiciel d'analyse de paris tennis (ATP / WTA). Il estime la probabilité de victoire de chaque joueur, la compare aux cotes des bookmakers, signale les paris à valeur positive et propose une mise (Kelly fractionné). Il suit aussi tes paris réels, ta bankroll et ta CLV.

> **Aucun pari n'est sûr.** Une value positive est une espérance de gain sur un grand nombre de paris, jamais une garantie sur un pari donné.

## Ce que dit le backtest (saisons 2019 → oct. 2026, 35 882 matchs)

| Constat | Chiffre | Conséquence |
|---|---|---|
| Notre modèle est moins précis que le marché | log loss ATP 0,610 contre 0,586 pour Pinnacle/Betfair (WTA 0,609 contre 0,583) | Il ne doit pas décider seul |
| Il n'apporte aucune information que les cotes n'ont pas déjà | poids du modèle entre −0,10 et +0,015 une fois combiné au marché, selon les saisons | Les stats publiques sont déjà dans les cotes |
| Parier sur la value « selon le modèle » perd | yield −8 % chez Bet365, −11 % avec une marge type ANJ, chaque saison | Stratégie abandonnée |
| Parier quand un bookmaker est plus généreux que la cote juste Pinnacle | yield +3,5 % chez Bet365 (1 617 paris), mais IC 95 % de −2,5 % à +9,5 % | Piste à valider en réel, **non prouvée** |

Conséquence pour le logiciel : par défaut, la probabilité de décision est celle du **marché sharp** (Pinnacle / Betfair Exchange sans marge). Le modèle sert de second avis et explique les facteurs du match. Ce réglage se change dans `config.yaml` (`decision_source`).

Détails, formules et sources : [docs/METHODOLOGIE.md](docs/METHODOLOGIE.md).

## Installation

Il faut Python 3.11 ou plus récent.

```bash
cd tennis-value
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp config.example.yaml config.yaml # puis adapter bankroll, seuils, clé API
```

Les fichiers historiques tennis-data.co.uk vont dans `data/raw/tennis-data/atp/AAAA.xlsx` et `data/raw/tennis-data/wta/AAAA.xlsx`. Ceux que tu m'as fournis (2016 → 2026) y sont déjà.

## L'application

Tout se fait depuis l'application, sans terminal : icône **Tennis Value** dans le lanceur (Chromebook, créée par `scripts/install_chromebook.sh`), `scripts/lancer.sh` ou `tennisvalue dashboard`. Elle s'ouvre dans le navigateur (http://localhost:8501), thème sombre façon site de paris, et récupère toute seule les relevés de GitHub à l'ouverture puis toutes les 30 minutes.

| Page | Contenu |
|---|---|
| **Matchs** | Matchs à venir par tournoi. Pour chaque joueur, bouton de cote avec la meilleure cote française, le bookmaker et ▲ ▼ depuis l'ouverture ; indicateur de **flux** (vers quel joueur va l'argent, et d'où il vient) ; badge VALUE sur les paris conseillés. Un clic sur le match ouvre le détail : **évolution des cotes de chaque bookmaker**, **évolution du flux** (consensus français contre Pinnacle / Betfair), tableau des cotes par bookmaker, avis du modèle. |
| **Tendances** | « Où va l'argent » : **argent réel** misé sur Polymarket (part de l'argent, montant, gros paris) ou **mouvements de cotes** des bookmakers (origine du mouvement), depuis l'ouverture, sur 24 h ou 6 h, avec une mini-courbe. |
| **Mon pari** (à droite) | Un clic sur une cote prépare le pari : choix du bookmaker, cote obtenue, mise conseillée (Kelly fractionné, plafonds), value à cette cote, gain potentiel ; **J'ai placé ce pari** l'enregistre (CLV automatique). L'application ne place aucun pari. |
| **Mes paris** | Bankroll, bénéfice, yield, CLV, en jeu ; paris en attente réglés en un clic ; courbe de bankroll ; historique ; ajout à la main. |
| **Analyse** | Modèle contre marché et backtest des stratégies. |
| **Réglages** | Bankroll, prudence des mises, plafonds, value minimale, cotes min/max, source de décision (écrits dans `config.yaml`) ; dépôt des nouveaux fichiers tennis-data (ATP ou WTA reconnu tout seul) puis recalcul du modèle. |

### Flux : ce qu'ils mesurent

Les bookmakers ne publient pas les montants misés sur chaque joueur. Le flux est **estimé à partir des cotes** : un bookmaker baisse la cote du joueur sur lequel l'argent arrive. À chaque relevé, la cote de chaque bookmaker est convertie en chance sans marge ; on suit la moyenne des bookmakers français (toute clé `_fr` de The Odds API : Betclic, Winamax, Unibet, PMU, Parions Sport, NetBet…) et celle de Pinnacle / Betfair Exchange (`market/flux.py`) :

- **Argent informé** : Pinnacle / Betfair ont bougé (parieurs avisés ou information nouvelle). Si les français n'ont pas suivi, leurs cotes sont en retard.
- **Grand public** : seuls les bookmakers français ont bougé (afflux de mises du public ou prudence du bookmaker).
- **Contradictoire** : français et sharp bougent en sens opposés.
- **Stable** : moins d'un point de mouvement.

La mesure dépend de la fréquence des relevés : 5 par jour suffisent pour suivre une tendance, pas pour saisir les mouvements de la dernière heure (un relevé toutes les 30 à 60 minutes demande une offre payante de The Odds API). Les relevés ne demandent que 7 bookmakers (Winamax, Betclic, Unibet, PMU, NetBet, Pinnacle, Betfair Exchange : réglage `odds_bookmakers`) : 1 crédit par tournoi ouvert et par relevé.

### Argent réel : Polymarket

Polymarket est un marché de prédiction (partenaire officiel de l'ATP depuis août 2026) où **chaque transaction est publique** : joueur, achat ou vente, montant, heure. On y lit donc de vrais flux d'argent, pas une estimation. À chaque relevé, GitHub Actions lit gratuitement ses API publiques (`ingest/polymarket.py`, sans compte ni clé) pour les matchs ATP (Challengers compris) et WTA : prix, volumes, liquidité, et toutes les transactions et le prix horaire depuis le relevé précédent. Les matchs sont rapprochés de ceux des bookmakers par les noms des joueurs et l'heure (`market/money.py`).

- **Part de l'argent** d'un joueur = achats de ce joueur + ventes de son adversaire, rapportés au total échangé (depuis l'ouverture, sur 24 h ou 6 h).
- **Argent net cumulé** : courbe qui monte quand l'argent va sur le joueur 1, descend quand il va sur le joueur 2.
- **Gros paris** : transactions d'au moins 500 $.

Les volumes vont de quelques centaines à plus de 100 000 $ par match : sur un petit marché, quelques gros paris font basculer la part. **Polymarket n'est pas autorisé en France** : ses données publiques sont lues pour l'analyse, on n'y parie pas.

## Ligne de commande

| Commande | Rôle |
|---|---|
| `tennisvalue build` | Recalcule tout : données, notes Elo et points, modèles, évaluation, backtest (≈ 40 s) |
| `tennisvalue fetch-odds` | Relève les cotes ANJ + Pinnacle + Betfair Exchange (The Odds API) et les horodate |
| `tennisvalue import-odds` | Ajoute à la base les relevés faits par GitHub Actions (après `git pull`) |
| `tennisvalue fetch-polymarket` | Relève les matchs Polymarket (gratuit) ; `import-polymarket` ajoute ceux de GitHub Actions |
| `tennisvalue scan` | Matchs à venir : probabilités, value, mise conseillée, facteurs clés, alertes |
| `tennisvalue dashboard` | Lance l'application (http://localhost:8501) |
| `tennisvalue bet-add "Alcaraz - Sinner" "Carlos Alcaraz" winamax_fr 2.25 10 --p 0.46` | Enregistre un pari placé |
| `tennisvalue bet-settle 1 gagne` | Règle un pari (`gagne`, `perdu`, `annule`) |
| `tennisvalue bankroll` | Bilan : bankroll, yield, drawdown, CLV moyenne |
| `pytest` | Lance les 47 tests (modèle de Markov, dé-margination, Kelly, absence de fuite de données, pages de l'application…) |

**Routine conseillée** : `fetch-odds` plusieurs fois par jour (planificateur de tâches Windows ou `cron`), pour mesurer l'ouverture, les mouvements et la clôture ; `scan` avant de parier ; `bankroll` chaque semaine. Chaque semaine, retélécharger les fichiers 2026 de tennis-data puis relancer `build` pour mettre les notes à jour : dans l'application, **Réglages → Nouveaux résultats de matchs** (déposer les deux fichiers, le modèle est recalculé).

### Clé The Odds API

Créer une clé sur https://the-odds-api.com, puis `export ODDS_API_KEY=...` (Windows : `setx ODDS_API_KEY ...`) ou la mettre dans `config.yaml`. Un relevé coûte « nombre de marchés × nombre de régions » crédits par tournoi : 2 crédits avec `h2h` sur `fr,eu`. Avec 4 tournois ouverts, un relevé coûte donc 8 crédits. L'offre gratuite (500 crédits par mois) suffit pour tester ; un suivi continu des mouvements demande une offre payante.

Pour une tâche planifiée, préférer `config.yaml` (ligne `odds_api_key: "..."`) : `cron` ne voit pas les variables définies avec `export`.

### Planifier la routine

`scripts/routine.sh` (macOS / Linux) et `scripts/routine.bat` (Windows) lancent `fetch-odds` puis `scan` et ajoutent la sortie à `logs/routine.log`. Exemple : toutes les 3 heures de 8 h à 20 h (8 h, 11 h, 14 h, 17 h, 20 h).

- macOS / Linux : `crontab -e`, puis ajouter `0 8-20/3 * * * /chemin/vers/tennis-value/scripts/routine.sh`
- Windows : `schtasks /Create /TN "TennisValue routine" /TR "\"C:\chemin\vers\tennis-value\scripts\routine.bat\"" /SC DAILY /ST 08:00 /RI 180 /DU 13:00 /F`

Coût : 5 relevés par jour × 2 crédits × nombre de tournois ouverts, soit environ 300 crédits par mois et par tournoi. Au-delà d'un tournoi ouvert, l'offre gratuite ne suffit plus : espacer les relevés ou passer à une offre payante. Le solde s'affiche dans le journal (« Crédits restants »).

### Relevés automatiques avec GitHub Actions (ordinateur éteint)

Le workflow `.github/workflows/fetch-odds.yml` lance `fetch-odds --export` à 8 h, 11 h, 14 h, 17 h et 20 h (heure de Paris en hiver, une heure plus tard en été), même ordinateur éteint, puis ajoute chaque relevé au dépôt dans `data/snapshots/`. Il remplace la routine `cron` : ne pas utiliser les deux, sinon chaque créneau coûte deux fois des crédits.

**En une commande (Chromebook avec Linux activé, Debian, Ubuntu)** : installe le projet dans `~/Paris-sportif`, lance `build`, ajoute l'icône Tennis Value au lanceur, enregistre la clé comme secret GitHub, lance un premier relevé puis ouvre l'application.

```bash
sudo apt-get update && sudo apt-get install -y curl && bash <(curl -fsSL https://raw.githubusercontent.com/clementdeguerre-beep/Paris-sportif/claude/zealous-sagan-qzgjyg/tennis-value/scripts/install_chromebook.sh)
```

**À la main** :

1. Sur GitHub : **Settings → Secrets and variables → Actions → New repository secret**, nom `ODDS_API_KEY`, valeur = la clé.
2. Onglet **Actions → Relevé des cotes → Run workflow** pour un premier essai.

Ensuite, avant de parier : bouton **Mettre à jour** de l'application, ou `scripts/sync.sh` (`git pull`, `import-odds` puis `scan`).

GitHub peut retarder une exécution planifiée de quelques minutes, voire la sauter quand ses serveurs sont chargés.

## Architecture

```
src/tennisvalue/
  ingest/tennisdata.py   lecture des fichiers historiques (résultats + cotes)
  ingest/oddsapi.py      cotes en direct (The Odds API), correspondance des noms de joueurs
  ingest/snapshots.py    export / import des relevés partagés par git (GitHub Actions)
  ratings/elo.py         Elo global et par surface
  ratings/points.py      note de domination aux points
  models/markov.py       point -> jeu -> tie-break -> set -> match (+ distribution des scores)
  models/features.py     variables d'avant-match, calculées sans fuite de données
  models/ensemble.py     régression logistique / gradient boosting, walk-forward
  market/devig.py        marge et probabilités sans marge (multiplicative, additive, power, Shin)
  market/staking.py      value, Kelly fractionné, plafonds
  market/movement.py     ouverture/clôture, mouvements brusques, cotes en retard, CLV
  backtest/              stratégies, métriques, IC bootstrap, simulation de bankroll
  tracking/bets.py       suivi des paris réels
  scan.py                scanner des matchs à venir
  sync.py                relevé local ou sur GitHub, git pull, état des données
  pipeline.py, cli.py    chaîne complète et ligne de commande
  market/flux.py         flux estimés : consensus français contre sharp, origine du mouvement
  ingest/polymarket.py   Polymarket : matchs, transactions, prix horaire (API publiques)
  market/money.py        argent réel : rapprochement des matchs, part de l'argent, gros paris
  web/api.py             application : API JSON (FastAPI) et page web
  web/static/            interface (HTML, CSS, JavaScript sans dépendance)
scripts/                 installation Chromebook, lanceur, synchro, routine cron
data/tennis.duckdb       base locale (créée par build)
```

Choix techniques : Python, DuckDB (base analytique dans un seul fichier, rapide, sans serveur), scikit-learn, FastAPI et une page web sans dépendance pour l'application. Pas de scraping : uniquement des fichiers publiés et des API.

## Limites connues de la v0.1

- **Pas de Challenger** : ni dans tennis-data, ni dans The Odds API. Ce sont pourtant les marchés les moins efficients. Il faut une API sous licence.
- **Pas de stats service / retour** (aces, 1res balles, balles de break) : la note aux points se déduit des jeux gagnés. La base TML-Database, qui contient ces stats, s'arrête en janvier 2026 et ne couvre que l'ATP.
- **Pas de météo, d'altitude, de vitesse de court, de voyages ni de signaux d'interviews (NLP)** : prévus, mais à n'ajouter que si un test montre qu'ils améliorent la prédiction au-delà du marché.
- **Marchés handicaps et totaux** : le moteur de Markov calcule déjà la distribution complète des scores, mais pas encore de backtest (il faut des cotes historiques de totaux).
- **Abandons** : traités comme paris annulés ; les règles varient selon les bookmakers.
- **Limitation des comptes** : les bookmakers limitent vite les parieurs gagnants, surtout sur les cotes en retard.

## Feuille de route

1. **v0.2 — validation en réel** : relevés de cotes réguliers, paris fictifs pendant 4 à 8 semaines, mesure de la CLV contre la clôture Pinnacle/Betfair. Pas d'argent réel avant une CLV moyenne positive sur plusieurs centaines de paris.
2. **v0.3 — Challengers + stats de service** via une API tennis sous licence.
3. **v0.4 — Handicaps et totaux de jeux/sets** à partir de la distribution du modèle de Markov.
4. **v0.5 — Signaux qualitatifs** (forfaits, déclarations de douleur) en NLP, gardés seulement s'ils battent le marché en test.

## Sources des données

- tennis-data.co.uk : résultats et cotes historiques ATP/WTA (usage personnel).
- TML-Database (github.com/Tennismylife/TML-Database) : stats de service ATP utilisées une fois pour mesurer la moyenne de points gagnés au service par surface. Conditions : usage non commercial uniquement.
- The Odds API : cotes en direct (ANJ, Pinnacle, Betfair Exchange).
