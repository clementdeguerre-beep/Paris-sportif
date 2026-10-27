"""Elo global et par surface.

Formule de mise à jour inspirée de FiveThirtyEight / Kovalchik (2016,
« Searching for the GOAT of tennis win prediction ») :
    K(n) = 250 / (n + 5)^0.4      n = nombre de matchs déjà joués par le joueur
Le K diminue avec l'expérience : un nouveau joueur bouge vite, un joueur
établi bouge lentement.

Probabilité que A batte B : 1 / (1 + 10^((R_B - R_A) / 400)).

Elo par surface : une note séparée par surface (dur, terre, gazon), mise à jour
uniquement sur cette surface. La note utilisée est un mélange
w x Elo global + (1 - w) x Elo surface (w = 0.5 par défaut, convention de Tennis Abstract).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

INITIAL = 1500.0


def _initial() -> float:
    return INITIAL


def k_factor(n: int) -> float:
    return 250.0 / (n + 5) ** 0.4


def win_prob(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


@dataclass
class EloSystem:
    blend_weight: float = 0.5
    rating: dict = field(default_factory=lambda: defaultdict(_initial))
    count: dict = field(default_factory=lambda: defaultdict(int))
    surf_rating: dict = field(default_factory=lambda: defaultdict(_initial))
    surf_count: dict = field(default_factory=lambda: defaultdict(int))

    def blended(self, player: str, surface: str) -> float:
        return self.blend_weight * self.rating[player] + (1 - self.blend_weight) * self.surf_rating[(player, surface)]

    def predict(self, a: str, b: str, surface: str) -> dict:
        return {
            "elo_a": self.rating[a],
            "elo_b": self.rating[b],
            "elo_surf_a": self.surf_rating[(a, surface)],
            "elo_surf_b": self.surf_rating[(b, surface)],
            "n_a": self.count[a],
            "n_b": self.count[b],
            "n_surf_a": self.surf_count[(a, surface)],
            "n_surf_b": self.surf_count[(b, surface)],
            "p_elo": win_prob(self.rating[a], self.rating[b]),
            "p_elo_surf": win_prob(self.surf_rating[(a, surface)], self.surf_rating[(b, surface)]),
            "p_elo_blend": win_prob(self.blended(a, surface), self.blended(b, surface)),
        }

    def update(self, winner: str, loser: str, surface: str) -> None:
        p = win_prob(self.rating[winner], self.rating[loser])
        kw, kl = k_factor(self.count[winner]), k_factor(self.count[loser])
        self.rating[winner] += kw * (1 - p)
        self.rating[loser] -= kl * (1 - p)
        self.count[winner] += 1
        self.count[loser] += 1

        sw, sl = (winner, surface), (loser, surface)
        ps = win_prob(self.surf_rating[sw], self.surf_rating[sl])
        kws, kls = k_factor(self.surf_count[sw]), k_factor(self.surf_count[sl])
        self.surf_rating[sw] += kws * (1 - ps)
        self.surf_rating[sl] -= kls * (1 - ps)
        self.surf_count[sw] += 1
        self.surf_count[sl] += 1
