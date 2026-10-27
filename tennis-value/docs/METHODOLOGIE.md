# Méthodologie — sources et calculs

Chaque chiffre du logiciel vient d'une des sources ci-dessous ou d'un calcul décrit ici. Quand une valeur est une hypothèse, elle est signalée comme telle.

## 1. Données

| Donnée | Source | Couverture | Remarque |
|---|---|---|---|
| Résultats, scores par set, classements, cotes Bet365 / Pinnacle / Betfair Exchange / meilleure / moyenne | Fichiers tennis-data.co.uk fournis par Clem (`data/raw/tennis-data`) | ATP et WTA, circuit principal, 03/01/2016 → 25/10/2026, 53 105 matchs | Une seule cote par bookmaker, sans heure de relevé. Pinnacle quasi absent en 2026 (2,9 % des matchs ATP), Betfair Exchange présent à partir de fin 2025 |
| Points gagnés au service (ATP) | TML-Database, saisons 2016-2025, 26 716 matchs avec stats | ATP | Usage non commercial uniquement |
| Cotes en direct | The Odds API, régions `fr` (sites ANJ) et `eu` (Pinnacle, Betfair Exchange) | Tournois ATP/WTA ouverts aux paris, pas de Challenger | Pinnacle y est relevé sur son site public, avec un léger délai selon la documentation de l'API |

Faits de contexte vérifiés :
- Pinnacle a fermé son API publique le 23/07/2025 ; l'accès reste possible sur demande pour des usages commerciaux ou académiques (documentation officielle github.com/pinnacleapi).
- Betfair Exchange n'est pas accessible aux parieurs français : la loi de 2010 n'autorise pas les bourses de paris. Ses cotes servent donc de référence, pas de lieu de pari.
- Le taux de retour aux joueurs des opérateurs agréés est plafonné à 85 % en moyenne annuelle (décret du 4 novembre 2020, art. 27, rappelé dans les décisions de sanction de l'ANJ).
- Écart de marge des sites français : les versions `.fr` de quatre marques affichaient environ 3,3 points de marge de plus que leurs autres versions (OddsPapi, échantillon de septembre 2026, sur du football). Cet écart sert au « proxy ANJ » du backtest : c'est une hypothèse, pas une mesure sur le tennis.

Marges médianes mesurées dans les fichiers tennis-data (2016-2026) : Pinnacle 2,6 %, Bet365 5,6 %, cote moyenne 5,6 %, Betfair Exchange 0,7 % (avant commission), meilleure cote 0,5 %.

## 2. Marge et probabilités sans marge (`market/devig.py`)

- Probabilité brute d'une cote o : 1/o.
- Marge : somme des 1/o du marché − 1. Exemple : 1,30 / 3,60 → 1/1,30 + 1/3,60 − 1 = 4,7 %.
- Probabilité sans marge, méthode **power** (par défaut) : p_i = (1/o_i)^k, avec k tel que la somme des p_i vaille 1. Elle met davantage de marge sur l'outsider, ce qui corrige le biais favori-outsider. Référence : Clarke, Kovalchik & Ingram (2017), « Adjusting bookmaker's odds to allow for overround ».
- Autres méthodes disponibles : multiplicative, additive, Shin. Sur un marché à deux issues, Shin donne le même résultat que la méthode additive.
- Garde-fou : un marché dont la marge sort de [−20 %, +30 %] est considéré comme une erreur de saisie et ignoré.

## 3. Modèles

### 3.1 Elo global et par surface (`ratings/elo.py`)
- P(A bat B) = 1 / (1 + 10^((R_B − R_A)/400)).
- Mise à jour : R ← R + K × (résultat − P), avec K = 250 / (n + 5)^0,4, où n est le nombre de matchs déjà joués (formule popularisée par FiveThirtyEight et Kovalchik, 2016).
- Elo surface : une note par surface, mise à jour sur cette surface seulement. Note mélangée = 50 % global + 50 % surface.
- Forfaits : ignorés. Abandons : pas de mise à jour de la note (score incomplet).

### 3.2 Note de domination aux points + modèle de Markov (`ratings/points.py`, `models/markov.py`)
- Chaque joueur a une note r : son excédent de probabilité de gagner un point, au service comme au retour.
- Contre B, A gagne un point sur son service avec pa = f + (r_A − r_B), et B avec pb = f − (r_A − r_B). f est la moyenne du circuit sur la surface :
  - ATP, mesuré (TML-Database 2016-2025) : dur 0,641, terre 0,618, gazon 0,659 ;
  - WTA, **hypothèse** : dur 0,565, terre 0,550, gazon 0,580. Aucune de nos sources ne contient les stats de service WTA. Test de sensibilité : déplacer f de ±0,02 change la log loss de moins de 0,001, donc la valeur n'influence pas le marché vainqueur. Elle comptera pour les totaux de jeux.
- Probabilité de garder son service : G(p) = p⁴(1 + 4q + 10q²) + 20p³q³ × p²/(1 − 2pq), avec q = 1 − p. Contrôle : G(0,6) = 0,7357.
- Tie-break (7 ou 10 points), set et match sont calculés par récurrence exacte, avec alternance du service. Règles du set décisif en Grand Chelem : super tie-break à 10 points depuis 2022 ; set à l'avantage à l'Open d'Australie et à Wimbledon jusqu'en 2018 et à Roland-Garros jusqu'en 2021 ; tie-break à 7 points à l'US Open jusqu'en 2021. Wimbledon 2019-2021 (tie-break à 12-12) est approximé par un set à l'avantage.
- Mise à jour de la note : la part de jeux gagnés est convertie en écart de points observé (inverse du modèle), puis la note bouge d'une fraction de l'erreur : taux = 0,15 / (n + 6)^0,3, écart observé plafonné à ±0,06, mélange 75 % global + 25 % surface. Ces réglages ont été choisis sur les saisons 2017-2018 seulement (`scripts/tune_params.py`).

### 3.3 Ensemble (`models/ensemble.py`, `models/features.py`)
Variables, toutes calculées avant le match et exprimées en différence joueur 1 − joueur 2 :
- logit de la proba Elo globale, logit de la proba Elo surface, logit de la proba Markov ;
- log(classement J2) − log(classement J1) (classement au début du tournoi, fourni par tennis-data ; joueur non classé = 1 500) ;
- expérience : log(1 + matchs joués) ;
- activité : jeux joués sur 14 jours et sur 7 jours ;
- inactivité : log(1 + jours depuis le dernier match), plafonné à 365 ;
- confrontations directes : (victoires J1 − victoires J2) / (total + 2) ;
- interaction Elo × format en 3 sets gagnants.

Combinaison par régression logistique L2, sans constante, entraînée sur les données doublées (chaque match dans les deux sens) pour garantir P(A bat B) = 1 − P(B bat A). Un gradient boosting est évalué en parallèle : il ne fait pas mieux (log loss 0,6108 contre 0,6102 en ATP).

Poids du modèle final (coefficients standardisés, ATP / WTA) : Markov 0,79 / 0,65 ; classement 0,33 / 0,21 ; inactivité −0,14 / −0,14 ; activité 14 j +0,07 / +0,12 ; charge 7 j −0,03 / −0,06. Lecture : plus de jeux joués sur 14 jours va avec plus de victoires (forme, ou simple effet de sélection : on joue plus quand on gagne), et ce n'est **pas** une preuve de l'effet de la fatigue. Aucun de ces facteurs n'a été testé isolément pour sa significativité.

## 4. Validation (`backtest/`, `pipeline.py`)

- **Walk-forward** : 2016 sert de chauffe, 2017-2018 de réglage ; chaque saison de 2019 à 2026 est prédite par un modèle entraîné uniquement sur les saisons précédentes. Les notes sont mises à jour après chaque prédiction. Le test `tests/test_no_leakage.py` vérifie qu'inverser les résultats d'un match et de tous les suivants ne change aucune variable des matchs antérieurs, ni celles de ce match.
- **Qualité des probabilités** : log loss = −moyenne[y log p + (1 − y) log(1 − p)] ; Brier = moyenne[(p − y)²] ; courbes de calibration par tranche de 5 points.
- **Test d'information** : pour chaque saison, régression logistique logit(résultat) ~ a × logit(p marché) + b × logit(p modèle), entraînée sur les saisons précédentes. Si b ≈ 0, le modèle n'apporte rien que le marché n'ait déjà.
- **Paris** : value = p × cote − 1 ; au plus un pari par match (le côté avec la plus forte value) ; cotes entre 1,30 et 5,00 ; abandons et forfaits annulés.
- **Indicateurs** : yield = bénéfice / total misé (mises à plat de 1 unité) ; intervalle de confiance à 95 % par bootstrap (2 000 tirages) ; t = yield / erreur standard ; « edge vs Pinnacle » = cote prise × proba juste Pinnacle − 1 (approximation de la CLV, faute d'horodatage) ; drawdown max = plus forte baisse depuis un sommet.
- **Simulation de bankroll** : départ 1 000 €, mise = 1/4 de Kelly, f* = (p × cote − 1)/(cote − 1), plafond 2 % par pari et 10 % d'exposition par jour.

Ordre de grandeur utile : sur n paris à une cote moyenne proche de 2,5, l'erreur standard du yield vaut environ 1,2/√n. Pour qu'un yield de +5 % se distingue de zéro (2 erreurs standard), il faut donc environ 2 400 paris. En dessous de quelques centaines de paris, un résultat positif ne prouve rien ; la CLV converge beaucoup plus vite et sert d'indicateur principal.

## 5. Décision en direct (`scan.py`)

- Proba de décision : marché sharp sans marge (moyenne Pinnacle / Betfair Exchange disponibles), par défaut. Options : modèle seul, ou value exigée selon les deux.
- Cote retenue : la meilleure cote parmi les sites ANJ suivis.
- Pari signalé si value ≥ `min_ev` (3 % par défaut) ; mise = Kelly fractionné plafonné.
- Alertes données : joueur absent de l'historique, moins de 20 matchs connus, surface supposée, historique non mis à jour depuis plus de 10 jours, absence de cote sharp.
- Mouvements : ouverture = premier relevé, clôture = dernier relevé avant le match ; mouvement brusque = proba sharp qui bouge d'au moins 3 points en moins de 60 minutes ; cote en retard = proba juste sharp supérieure d'au moins 2,5 points à la proba brute du bookmaker.
- CLV d'un pari = cote prise × proba juste de clôture − 1.
