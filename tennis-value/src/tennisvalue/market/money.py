"""Flux d'argent réels (Polymarket) : rapprochement avec les matchs des bookmakers, part de l'argent.

Sur Polymarket, acheter le joueur A ou vendre le joueur B revient à miser sur A. La part
de l'argent sur A = (achats de A + ventes de B) / total des transactions, en dollars.
Contrairement aux flux estimés d'après les cotes (market/flux.py), ce sont des montants
réellement échangés ; ils restent ceux d'un seul marché, souvent modeste.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tennisvalue.ingest.tennisdata import norm_name

MAX_GAP_H = 12          # écart maximal entre l'heure de début Polymarket et celle des bookmakers
BIG_TRADE_USD = 500     # « gros pari »


def link(events: pd.DataFrame, poly: pd.DataFrame) -> dict:
    """event_id (bookmakers) -> (cond Polymarket, ordre inversé ?), par noms des joueurs et heure de début.

    `events` : event_id, player_1, player_2, commence_time ; `poly` : cond, player_1, player_2, game_start.
    Noms complets normalisés (accents, tirets), puis noms de famille à défaut.
    """
    out = {}
    if events.empty or poly.empty:
        return out
    poly = poly.drop_duplicates("cond")
    keys = {"full": lambda n: norm_name(n), "last": lambda n: norm_name(n).split()[-1]}
    for e in events.itertuples():
        near = poly[(poly["game_start"] - e.commence_time).abs() <= pd.Timedelta(hours=MAX_GAP_H)]
        for k in ("full", "last"):
            f = keys[k]
            a, b = f(e.player_1), f(e.player_2)
            hit = near[[{f(p1), f(p2)} == {a, b} for p1, p2 in zip(near["player_1"], near["player_2"])]]
            if len(hit) == 1:
                r = hit.iloc[0]
                out[e.event_id] = (r["cond"], f(r["player_1"]) != a)
                break
    return out


def _toward_1(t: pd.DataFrame) -> pd.Series:
    """Montant de chaque transaction misé sur le joueur 1 (+) ou sur le joueur 2 (-)."""
    on_1 = ((t["outcome_index"] == 0) & (t["side"] == "BUY")) | ((t["outcome_index"] == 1) & (t["side"] == "SELL"))
    return np.where(on_1, t["usd"], -t["usd"])


def pressure(trades: pd.DataFrame, since=None) -> dict:
    """Argent misé sur chaque joueur (en $) et part du joueur 1, éventuellement depuis `since`."""
    t = trades if since is None else trades[trades["ts"] >= since]
    signed = _toward_1(t) if len(t) else np.array([])
    usd_1, usd_2 = float(signed[signed > 0].sum()), float(-signed[signed < 0].sum())
    total = usd_1 + usd_2
    return {"usd_1": usd_1, "usd_2": usd_2, "share_1": usd_1 / total if total else np.nan, "n": int(len(t)),
            "n_big": int((t["usd"] >= BIG_TRADE_USD).sum()) if len(t) else 0}


def cumulative(trades: pd.DataFrame, freq: str = "h") -> pd.DataFrame:
    """Argent net cumulé vers le joueur 1 (en $), par heure : monte quand l'argent va sur lui."""
    if trades.empty:
        return pd.DataFrame(columns=["ts", "net_1"])
    t = trades.assign(signed=_toward_1(trades)).sort_values("ts")
    h = t.groupby(t["ts"].dt.floor(freq))["signed"].sum().cumsum()
    return pd.DataFrame({"ts": h.index, "net_1": h.to_numpy()})


def big_trades(trades: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """Plus grosses transactions (au moins BIG_TRADE_USD), les plus grosses d'abord."""
    t = trades[trades["usd"] >= BIG_TRADE_USD]
    return t.sort_values("usd", ascending=False).head(n)
