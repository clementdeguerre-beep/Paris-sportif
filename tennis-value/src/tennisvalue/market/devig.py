"""Marge du bookmaker et probabilités implicites « sans marge ».

Une cote décimale o implique une probabilité brute 1/o. La somme des
probabilités brutes d'un marché dépasse 1 : l'excédent est la marge
(overround). Il existe plusieurs façons de la retirer ; elles diffèrent sur la
répartition de la marge entre favori et outsider (biais favori-outsider).

- multiplicative : divise chaque proba par la somme (marge répartie au prorata) ;
- additive       : retire la même quantité à chaque issue ;
- power          : p_i = (1/o_i)^k, k choisi pour que la somme vaille 1.
                   Charge davantage la marge sur l'outsider. Méthode retenue par
                   défaut pour le tennis, d'après Clarke, Kovalchik & Ingram (2017),
                   « Adjusting bookmaker's odds to allow for overround » ;
- shin           : modèle de Shin (1993), qui suppose une part de parieurs initiés.
"""
from __future__ import annotations

import math
from typing import Sequence

from scipy.optimize import brentq


def implied(odds: Sequence[float]) -> list[float]:
    return [1.0 / o for o in odds]


def margin(odds: Sequence[float]) -> float:
    """Marge du bookmaker : somme des probabilités brutes - 1 (0.05 = 5 %)."""
    return sum(implied(odds)) - 1.0


def multiplicative(odds: Sequence[float]) -> list[float]:
    raw = implied(odds)
    s = sum(raw)
    return [r / s for r in raw]


def additive(odds: Sequence[float]) -> list[float]:
    raw = implied(odds)
    excess = (sum(raw) - 1.0) / len(raw)
    p = [max(r - excess, 1e-9) for r in raw]
    s = sum(p)
    return [x / s for x in p]


def power(odds: Sequence[float]) -> list[float]:
    raw = implied(odds)
    if abs(sum(raw) - 1.0) < 1e-12:
        return raw
    try:
        k = brentq(lambda k: sum(r**k for r in raw) - 1.0, 0.05, 50.0)
    except ValueError:
        return multiplicative(odds)
    return [r**k for r in raw]


def shin(odds: Sequence[float]) -> list[float]:
    raw = implied(odds)
    total = sum(raw)
    if total <= 1.0:  # pas de marge positive : le modèle de Shin ne s'applique pas
        return multiplicative(odds)

    def probs(z: float) -> list[float]:
        return [
            (math.sqrt(z * z + 4 * (1 - z) * r * r / total) - z) / (2 * (1 - z))
            for r in raw
        ]

    z = brentq(lambda z: sum(probs(z)) - 1.0, 0.0, 0.5)
    return probs(z)


# Garde-fous contre les erreurs de saisie dans les données de cotes.
MAX_MARGIN = 0.30
MIN_MARGIN = -0.20

METHODS = {
    "multiplicative": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
}


def fair_probs(odds: Sequence[float], method: str = "power") -> list[float]:
    """Probabilités sans marge. Renvoie des NaN si une cote est absente ou invalide."""
    if any(o is None or not math.isfinite(o) or o <= 1.0 for o in odds):
        return [math.nan] * len(odds)
    m = margin(odds)
    if m > MAX_MARGIN or m < MIN_MARGIN:  # cote manifestement erronée dans la source
        return [math.nan] * len(odds)
    return METHODS[method](odds)


def fair_two_way(o1: float, o2: float, method: str = "power") -> tuple[float, float]:
    p = fair_probs([o1, o2], method)
    return p[0], p[1]
