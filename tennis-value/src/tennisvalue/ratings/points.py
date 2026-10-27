"""Note de « domination aux points » + modèle de Markov.

Idée : chaque joueur a une note r = combien de points (en probabilité) il gagne
de plus qu'un joueur moyen, au service comme au retour. Pour A contre B :
    proba que A gagne un point sur son service  pa = f + (r_A - r_B)
    proba que B gagne un point sur son service  pb = f - (r_A - r_B)
où f est la moyenne du circuit (proba de gagner un point au service) sur la
surface. Le modèle de Markov transforme (pa, pb) en probabilité de gagner le
match, en tenant compte du format (2 ou 3 sets gagnants, règle du set décisif).

Mise à jour : après chaque match terminé, on observe la part de jeux gagnés,
on la convertit en écart de points observé (inverse du modèle de Markov) et on
corrige les notes d'une fraction de l'erreur (pas d'apprentissage décroissant
avec l'expérience, comme le K de l'Elo).

Limite assumée du MVP : les fichiers tennis-data ne contiennent pas les stats
de service, donc la note ne sépare pas service et retour (prévu en V2 avec une
source de stats point par point).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from tennisvalue.models.markov import expected_game_share, match_prob

# Proba moyenne de gagner un point au service, par circuit et surface.
# ATP : mesurée sur TML-Database 2016-2025 (26 716 matchs, colonnes w/l_1stWon, 2ndWon, svpt).
# WTA : aucune de nos sources ne contient les stats de service -> HYPOTHÈSE (ordre de
# grandeur usuel du circuit féminin). Test de sensibilité (scripts/tune_params.py) :
# décaler f de ±0.02 change la log loss du vainqueur de moins de 0.001, donc sans
# effet sur le marché vainqueur ; en revanche f comptera pour les totaux de jeux (V2).
SERVE_BASELINE = {
    ("ATP", "Hard"): 0.641,
    ("ATP", "Clay"): 0.618,
    ("ATP", "Grass"): 0.659,
    ("ATP", "Carpet"): 0.641,
    ("WTA", "Hard"): 0.565,
    ("WTA", "Clay"): 0.550,
    ("WTA", "Grass"): 0.580,
    ("WTA", "Carpet"): 0.565,
}

DELTA_GRID = np.round(np.arange(-0.35, 0.3501, 0.001), 3)
_share_cache: dict = {}


def _share_curve(f: float) -> np.ndarray:
    if f not in _share_cache:
        _share_cache[f] = np.array([expected_game_share(f + d, f - d) for d in DELTA_GRID])
    return _share_cache[f]


def delta_from_share(share: float, f: float) -> float:
    """Écart de points (r_A - r_B) cohérent avec une part de jeux observée."""
    return float(np.interp(share, _share_curve(f), DELTA_GRID))


@dataclass
class PointRating:
    # Réglés sur 2017-2018 uniquement (scripts/tune_params.py), identiques ATP et WTA.
    lr0: float = 0.15
    lr_power: float = 0.3
    lr_offset: float = 6.0
    blend_weight: float = 0.75
    max_obs_delta: float = 0.06
    baseline: dict = field(default_factory=lambda: dict(SERVE_BASELINE))
    r: dict = field(default_factory=lambda: defaultdict(float))
    n: dict = field(default_factory=lambda: defaultdict(int))
    rs: dict = field(default_factory=lambda: defaultdict(float))
    ns: dict = field(default_factory=lambda: defaultdict(int))

    def lr(self, n: int) -> float:
        return self.lr0 / (n + self.lr_offset) ** self.lr_power

    def f(self, tour: str, surface: str) -> float:
        return self.baseline.get((tour, surface), self.baseline[(tour, "Hard")])

    def delta(self, a: str, b: str, surface: str) -> float:
        w = self.blend_weight
        return w * (self.r[a] - self.r[b]) + (1 - w) * (self.rs[(a, surface)] - self.rs[(b, surface)])

    def predict(self, a: str, b: str, tour: str, surface: str, best_of: int, final_rule: str) -> dict:
        d = float(np.clip(self.delta(a, b, surface), -0.3, 0.3))
        f = self.f(tour, surface)
        pa = float(np.clip(f + d, 0.30, 0.95))
        pb = float(np.clip(f - d, 0.30, 0.95))
        return {"pts_delta": d, "p_pts": match_prob(pa, pb, best_of, final_rule)}

    def update(self, winner: str, loser: str, tour: str, surface: str, w_games: float, l_games: float) -> None:
        total = w_games + l_games
        if total < 6:  # score incomplet ou aberrant
            return
        f = self.f(tour, surface)
        d_obs = float(np.clip(delta_from_share(w_games / total, f), -self.max_obs_delta, self.max_obs_delta))
        err = d_obs - self.delta(winner, loser, surface)
        self.r[winner] += self.lr(self.n[winner]) * err
        self.r[loser] -= self.lr(self.n[loser]) * err
        sw, sl = (winner, surface), (loser, surface)
        self.rs[sw] += self.lr(self.ns[sw]) * err
        self.rs[sl] -= self.lr(self.ns[sl]) * err
        self.n[winner] += 1
        self.n[loser] += 1
        self.ns[sw] += 1
        self.ns[sl] += 1
