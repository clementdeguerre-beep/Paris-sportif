"""Indicateurs de qualité des probabilités et de rentabilité des paris."""
from __future__ import annotations

import numpy as np
import pandas as pd


def log_loss(p, y) -> float:
    """Perte logarithmique : plus bas = meilleur. Pénalise fortement l'excès de confiance."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p, y) -> float:
    """Score de Brier : erreur quadratique moyenne de la probabilité. Plus bas = meilleur."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def accuracy(p, y) -> float:
    return float(np.mean((np.asarray(p) > 0.5) == np.asarray(y)))


def calibration_table(p, y, bins: int = 10) -> pd.DataFrame:
    """Probabilité moyenne annoncée vs fréquence de victoire observée, par tranche."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    # on lit chaque match du point de vue du favori annoncé (p >= 0.5)
    fav_p = np.where(p >= 0.5, p, 1 - p)
    fav_y = np.where(p >= 0.5, y, 1 - y)
    edges = np.linspace(0.5, 1.0, bins + 1)
    idx = np.clip(np.digitize(fav_p, edges) - 1, 0, bins - 1)
    df = pd.DataFrame({"bin": idx, "p": fav_p, "y": fav_y})
    out = df.groupby("bin").agg(n=("y", "size"), p_moy=("p", "mean"), freq_obs=("y", "mean")).reset_index()
    out["tranche"] = [f"{edges[b]:.0%}-{edges[b + 1]:.0%}" for b in out["bin"]]
    return out[["tranche", "n", "p_moy", "freq_obs"]]


def max_drawdown(equity: np.ndarray) -> float:
    """Plus forte baisse depuis un sommet, en % du sommet."""
    equity = np.asarray(equity, float)
    if len(equity) == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    return float(np.max((peaks - equity) / peaks))


def betting_summary(returns: np.ndarray, odds: np.ndarray, n_boot: int = 2000, seed: int = 0) -> dict:
    """Synthèse pour des mises à plat de 1 unité.

    returns : gain net par pari (cote - 1 si gagné, -1 si perdu).
    Yield = bénéfice / total misé. IC 95 % par bootstrap.
    """
    returns = np.asarray(returns, float)
    n = len(returns)
    if n == 0:
        return {"n_paris": 0}
    rng = np.random.default_rng(seed)
    boots = rng.choice(returns, size=(n_boot, n), replace=True).mean(axis=1)
    se = returns.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    t = returns.mean() / se if se and se > 0 else np.nan
    return {
        "n_paris": n,
        "reussite": float(np.mean(returns > 0)),
        "cote_moy": float(np.mean(odds)),
        "benefice_u": float(returns.sum()),
        "yield": float(returns.mean()),
        "ic95_bas": float(np.percentile(boots, 2.5)),
        "ic95_haut": float(np.percentile(boots, 97.5)),
        "t_stat": float(t),
    }


def odds_bucket(odds: pd.Series) -> pd.Series:
    bins = [1.0, 1.5, 2.0, 2.5, 3.5, 5.0, 1000]
    labels = ["1.01-1.49", "1.50-1.99", "2.00-2.49", "2.50-3.49", "3.50-4.99", "5.00+"]
    return pd.cut(odds, bins=bins, labels=labels, right=False)
