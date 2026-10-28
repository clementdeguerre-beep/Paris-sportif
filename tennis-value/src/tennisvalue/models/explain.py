"""Pourquoi le modèle donne telle chance : effet de chaque famille de facteurs, en points de %.

Effet d'une famille = chance du joueur 1 avec tous les facteurs - chance du joueur 1 si
cette famille ne départageait pas les deux joueurs (ses variables mises à 0, c'est-à-dire
à égalité). Positif : la famille avantage le joueur 1. Les effets ne s'additionnent pas
exactement (la courbe logistique n'est pas linéaire), mais leur signe et leur ordre de
grandeur se lisent directement.
"""
from __future__ import annotations

import numpy as np

from tennisvalue.models.features import FEATURES

GROUPS = {
    "niveau": ("Niveau général", ["x_logit_elo", "x_logit_pts", "x_log_rank_ratio", "x_exp", "x_bo5_elo"]),
    "surface": ("Surface", ["x_logit_elo_surf", "x_surf_form"]),
    "forme": ("Forme récente", ["x_form10", "x_rank_mom", "x_m4"]),
    "tournoi": ("Tournoi", ["x_tourn_form"]),
    "fatigue": ("Fatigue", ["x_games14", "x_games7", "x_last_games"]),
    "physique": ("Blessure / reprise", ["x_rust", "x_rusty", "x_injury"]),
    "mental": ("Mental", ["x_clutch_tb", "x_clutch_dec", "x_comeback"]),
    "h2h": ("Face-à-face", ["x_h2h"]),
}


def proba(model, X: np.ndarray) -> np.ndarray:
    """Chance du joueur 1, symétrisée (P(A bat B) = 1 - P(B bat A))."""
    return 0.5 * (model.predict_proba(X)[:, 1] + 1 - model.predict_proba(-X)[:, 1])


def effects(model, X: np.ndarray) -> dict[str, np.ndarray]:
    """Effet de chaque famille de facteurs (en probabilité, de -1 à 1) pour chaque ligne de X."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    full = proba(model, X)
    out = {}
    for key, (_, cols) in GROUPS.items():
        Xg = X.copy()
        Xg[:, [FEATURES.index(c) for c in cols]] = 0.0
        out[key] = full - proba(model, Xg)
    return out
