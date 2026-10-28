"""Jeux de données synthétiques pour les tests (indépendants des fichiers réels)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tennisvalue.ingest.tennisdata import match_id, norm_name


def make_matches(n: int = 600, n_players: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    skill = rng.normal(0, 1, n_players)
    names = [f"Joueur{i} A." for i in range(n_players)]
    rows = []
    d0 = date(2016, 1, 4)
    for k in range(n):
        i, j = rng.choice(n_players, 2, replace=False)
        p = 1 / (1 + np.exp(-(skill[i] - skill[j])))
        w, l = (i, j) if rng.random() < p else (j, i)
        lg = int(rng.integers(3, 10))
        rows.append({
            "tour": "ATP", "date": d0 + timedelta(days=k // 4), "tournament": f"T{k // 40}",
            "location": "X", "tier": "ATP250", "court": "Outdoor",
            "surface": ["Hard", "Clay", "Grass"][k % 3], "round": "1st Round", "best_of": 3,
            "winner": names[w], "loser": names[l], "w_rank": float(w + 1), "l_rank": float(l + 1),
            "w_pts": np.nan, "l_pts": np.nan, "comment": "Completed", "w_sets": 2.0, "l_sets": 0.0,
            "w1": 6.0, "l1": float(lg // 2), "w2": 6.0, "l2": float(lg - lg // 2),
            "w3": np.nan, "l3": np.nan, "w4": np.nan, "l4": np.nan, "w5": np.nan, "l5": np.nan,
            "w_games": 12.0, "l_games": float(lg), "n_sets": 2.0, "tb_sets": 0.0,
            "season": 2016 + k // 200,
        })
        o_w = round(1 / max(p if w == i else 1 - p, 0.05) * 0.95, 2)
        o_l = round(1 / max(1 - (p if w == i else 1 - p), 0.05) * 0.95, 2)
        for bk in ("b365", "ps", "bfe", "max", "avg"):
            rows[-1][f"odds_{bk}_w"] = max(o_w, 1.01)
            rows[-1][f"odds_{bk}_l"] = max(o_l, 1.01)
    df = pd.DataFrame(rows)
    df["winner_key"] = df["winner"].map(norm_name)
    df["loser_key"] = df["loser"].map(norm_name)
    df["match_id"] = [match_id(r) for r in df.itertuples()]
    df["completed"] = True
    df["walkover"] = False
    df["file_row"] = np.arange(len(df))
    df["seq"] = np.arange(len(df))
    return df


def make_live(n: int = 1600, n_players: int = 24, seed: int = 3) -> dict:
    """Modèle final (comme live_model.pkl) appris sur des matchs synthétiques."""
    from tennisvalue.models import ensemble
    from tennisvalue.models.features import FEATURE_VERSION, build_features

    m = make_matches(n=n, n_players=n_players, seed=seed)
    f, states = build_features(m)
    return {"models": {"ATP": ensemble.fit(f[f.completed], "logit")}, "states": states,
            "last_rank": {("ATP", k): 10.0 for k in m["winner_key"]}, "feature_version": FEATURE_VERSION,
            "data_until": str(m.date.max()), "built_at": "2026-10-28T10:00"}


@pytest.fixture
def matches() -> pd.DataFrame:
    return make_matches()
