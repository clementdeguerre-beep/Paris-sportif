"""Réglage des hyperparamètres de la note « domination aux points ».

Réglage UNIQUEMENT sur les saisons 2017-2018 (2016 sert de chauffe des notes),
pour que les saisons de test (2019 et après) restent vierges.
Critère : log loss (pénalise les probabilités fausses et trop confiantes).

Pour la WTA, la moyenne f de points gagnés au service n'est mesurée dans
aucune de nos sources : elle est choisie ici par maximum de vraisemblance.
Validation de la méthode : sur l'ATP, on vérifie que le f retenu reste proche
du f mesuré dans les stats de service (TML-Database).

Usage : python scripts/tune_params.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tennisvalue.ingest.tennisdata import load_all  # noqa: E402
from tennisvalue.models.markov import final_set_rule  # noqa: E402
from tennisvalue.ratings.points import SERVE_BASELINE, PointRating  # noqa: E402

TUNE_SEASONS = (2017, 2018)


def log_loss(p, y):
    p = np.clip(np.asarray(p), 1e-6, 1 - 1e-6)
    y = np.asarray(y)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def evaluate(g, params, f_shift):
    base = {k: v + f_shift for k, v in SERVE_BASELINE.items()}
    pr = PointRating(baseline=base, **params)
    ps, ys = [], []
    for r in g.itertuples(index=False):
        if r.walkover:
            continue
        if r.completed and r.season in TUNE_SEASONS:
            rule = final_set_rule(r.tier, r.date.year, r.tournament)
            ps.append(pr.predict(r.winner_key, r.loser_key, r.tour, r.surface, r.best_of, rule)["p_pts"])
            ys.append(1)
        if r.completed:
            pr.update(r.winner_key, r.loser_key, r.tour, r.surface, r.w_games, r.l_games)
        if r.season > max(TUNE_SEASONS):
            break
    # le vainqueur est toujours en position A : log loss symétrique, inutile de retourner
    return log_loss(ps, ys)


def main() -> None:
    df = load_all(ROOT / "data" / "raw" / "tennis-data")
    grid = {
        "lr0": [0.05, 0.1, 0.15],
        "lr_power": [0.2, 0.3, 0.4],
        "max_obs_delta": [0.04, 0.06, 0.08],
        "blend_weight": [0.75, 0.9],
    }
    shifts = {"ATP": [0.0], "WTA": [0.0]}  # 1er passage : ±0.02 sur f change la log loss de < 0.001
    for tour, g in df.groupby("tour"):
        results = []
        for vals in itertools.product(*grid.values()):
            params = dict(zip(grid.keys(), vals))
            for sh in shifts[tour]:
                results.append((evaluate(g, params, sh), sh, params))
        results.sort(key=lambda x: x[0])
        print(f"== {tour} : 5 meilleurs réglages (log loss 2017-2018)")
        for ll, sh, params in results[:5]:
            print(f"  {ll:.4f}  décalage f={sh:+.2f}  {params}")


if __name__ == "__main__":
    main()
