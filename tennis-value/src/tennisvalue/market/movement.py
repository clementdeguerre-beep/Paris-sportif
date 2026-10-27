"""Lecture des mouvements de cotes à partir des relevés horodatés.

Pour chaque match et chaque bookmaker :
- cote d'ouverture = premier relevé ; cote de clôture = dernier relevé avant le début ;
- mouvement = variation de la probabilité sans marge (en points de %), avec son
  sens (le joueur 1 « raccourcit » si sa proba monte) et sa vitesse (points/heure).

Signaux :
- mouvement brusque (« steam ») : la proba sharp d'un joueur bouge d'au moins
  X points en moins de Y minutes. Souvent le signe d'argent informé… ou d'une
  info publique (forfait, blessure). À vérifier avant d'agir ;
- cote en retard : un bookmaker propose une cote dont la proba sans marge
  s'écarte d'au moins Z points de la proba sharp actuelle, dans le sens
  favorable au parieur ;
- CLV (Closing Line Value) : cote prise / cote juste de clôture - 1.
  Une CLV positive en moyenne sur beaucoup de paris est le meilleur indicateur
  disponible d'un vrai avantage (bien plus stable que le résultat des paris).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tennisvalue.market.devig import fair_two_way


def with_fair_probs(snaps: pd.DataFrame, method: str = "power") -> pd.DataFrame:
    s = snaps.copy()
    fp = [fair_two_way(a, b, method) for a, b in zip(s["odds_1"], s["odds_2"])]
    s["fair_1"] = [p[0] for p in fp]
    s["margin"] = 1 / s["odds_1"] + 1 / s["odds_2"] - 1
    return s


def open_close(snaps: pd.DataFrame) -> pd.DataFrame:
    """Ouverture, clôture, mouvement et vitesse par (match, bookmaker)."""
    s = with_fair_probs(snaps)
    s = s[s["fetched_at"] <= s["commence_time"]].sort_values("fetched_at")
    g = s.groupby(["event_id", "book"])
    first, last = g.first(), g.last()
    hours = (last["fetched_at"] - first["fetched_at"]).dt.total_seconds() / 3600
    out = pd.DataFrame({
        "player_1": first["player_1"], "player_2": first["player_2"],
        "commence_time": first["commence_time"],
        "odds_open_1": first["odds_1"], "odds_open_2": first["odds_2"],
        "odds_close_1": last["odds_1"], "odds_close_2": last["odds_2"],
        "fair_open_1": first["fair_1"], "fair_close_1": last["fair_1"],
        "margin": last["margin"], "n_snapshots": g.size(),
    })
    out["move_pp_1"] = 100 * (out["fair_close_1"] - out["fair_open_1"])
    out["direction"] = np.where(out["move_pp_1"] > 0, "joueur 1 raccourcit",
                                np.where(out["move_pp_1"] < 0, "joueur 2 raccourcit", "stable"))
    out["speed_pp_per_h"] = out["move_pp_1"] / hours.replace(0, np.nan)
    return out.reset_index()


def steam_moves(snaps: pd.DataFrame, sharp_books: tuple, move_pp: float, window_min: int) -> pd.DataFrame:
    """Mouvements brusques sur les bookmakers sharp."""
    s = with_fair_probs(snaps[snaps["book"].isin(sharp_books)]).sort_values("fetched_at")
    alerts = []
    for (ev, book), g in s.groupby(["event_id", "book"]):
        t = g["fetched_at"].to_numpy()
        p = g["fair_1"].to_numpy()
        for j in range(1, len(g)):
            lo = np.searchsorted(t, t[j] - np.timedelta64(window_min, "m"))
            if lo >= j:
                continue
            delta = 100 * (p[j] - p[lo:j])
            k = int(np.argmax(np.abs(delta)))
            if abs(delta[k]) >= move_pp:
                alerts.append({
                    "event_id": ev, "book": book, "at": g["fetched_at"].iloc[j],
                    "player_1": g["player_1"].iloc[0], "player_2": g["player_2"].iloc[0],
                    "move_pp_1": float(delta[k]),
                    "minutes": float((t[j] - t[lo + k]) / np.timedelta64(1, "m")),
                })
                break  # une alerte par match et bookmaker suffit
    return pd.DataFrame(alerts)


def stale_lines(latest: pd.DataFrame, sharp_books: tuple, gap_pp: float) -> pd.DataFrame:
    """Cotes en retard : comparaison du dernier relevé de chaque bookmaker à la proba sharp.

    `latest` : un relevé par (match, bookmaker), le plus récent.
    Renvoie les côtés où la cote du bookmaker est plus généreuse que la cote juste sharp.
    """
    s = with_fair_probs(latest)
    sharp = s[s["book"].isin(sharp_books)].groupby("event_id")["fair_1"].mean().rename("sharp_1")
    s = s.join(sharp, on="event_id")
    s = s[~s["book"].isin(sharp_books) & s["sharp_1"].notna()]
    rows = []
    for side in (1, 2):
        p_sharp = s["sharp_1"] if side == 1 else 1 - s["sharp_1"]
        odds = s[f"odds_{side}"]
        ev = p_sharp * odds - 1
        gap = 100 * (p_sharp - 1 / odds)  # proba juste - proba implicite brute du bookmaker
        sel = gap >= gap_pp
        part = s[sel].assign(
            side=side, selection=s.loc[sel, f"player_{side}"], odds=odds[sel],
            p_sharp=p_sharp[sel], ev_sharp=ev[sel], gap_pp=gap[sel],
        )
        rows.append(part)
    cols = ["event_id", "commence_time", "player_1", "player_2", "book", "selection", "odds",
            "p_sharp", "ev_sharp", "gap_pp", "book_update"]
    out = pd.concat(rows) if rows else pd.DataFrame(columns=cols)
    return out[cols].sort_values("ev_sharp", ascending=False).reset_index(drop=True)


def clv(odds_taken: float, closing_fair_prob: float) -> float:
    """CLV d'un pari : cote prise x proba juste de clôture - 1 (0.03 = +3 %)."""
    if not closing_fair_prob or np.isnan(closing_fair_prob):
        return np.nan
    return odds_taken * closing_fair_prob - 1
