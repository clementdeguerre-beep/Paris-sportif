"""Suivi de mes paris réels : enregistrement, règlement, CLV, bankroll."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from tennisvalue import db
from tennisvalue.market.devig import fair_two_way
from tennisvalue.market.movement import clv


def add_bet(con, *, match: str, selection: str, book: str, odds: float, stake: float,
            p_decision: float | None = None, event_id: str | None = None, tour: str | None = None,
            note: str = "") -> int:
    ev = p_decision * odds - 1 if p_decision is not None else None
    row = con.execute(
        """INSERT INTO bets (placed_at, event_id, tour, match, selection, book, odds, stake, p_decision, ev, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING bet_id""",
        [datetime.now(), event_id, tour, match, selection, book, odds, stake, p_decision, ev, note],
    ).fetchone()
    return int(row[0])


def settle_bet(con, bet_id: int, result: str) -> None:
    """result : « gagne », « perdu » ou « annule »."""
    if result not in ("gagne", "perdu", "annule"):
        raise ValueError("résultat attendu : gagne, perdu ou annule")
    odds, stake = con.execute("SELECT odds, stake FROM bets WHERE bet_id = ?", [bet_id]).fetchone()
    pnl = {"gagne": stake * (odds - 1), "perdu": -stake, "annule": 0.0}[result]
    con.execute("UPDATE bets SET status = ?, pnl = ? WHERE bet_id = ?", [result, pnl, bet_id])


def delete_bet(con, bet_id: int) -> None:
    """Supprime un pari saisi par erreur."""
    con.execute("DELETE FROM bets WHERE bet_id = ?", [bet_id])


def update_clv(con, sharp_books: tuple) -> int:
    """Calcule la CLV des paris liés à un match suivi (event_id) dont la cote de clôture est connue.

    Seulement une fois le match commencé : avant, le dernier relevé n'est pas encore la clôture.
    """
    bets = db.read(con, "SELECT bet_id, event_id, selection, odds FROM bets WHERE event_id IS NOT NULL AND clv IS NULL")
    n = 0
    for b in bets.itertuples():
        snaps = db.read(con, """
            SELECT * FROM odds_snapshots WHERE event_id = ? AND fetched_at <= commence_time
              AND commence_time <= now()::TIMESTAMP
            ORDER BY fetched_at""", [b.event_id])
        snaps = snaps[snaps["book"].isin(sharp_books)]
        if snaps.empty:
            continue
        last = snaps.groupby("book").last()
        side = 1 if last["player_1"].iloc[0] == b.selection else 2
        probs = [fair_two_way(r.odds_1, r.odds_2)[side - 1] for r in last.itertuples()]
        p_close = float(np.nanmean(probs))
        con.execute("UPDATE bets SET closing_odds_fair = ?, clv = ? WHERE bet_id = ?",
                    [1 / p_close, clv(b.odds, p_close), b.bet_id])
        n += 1
    return n


def bankroll_report(con, initial: float) -> dict:
    bets = db.read(con, "SELECT * FROM bets ORDER BY placed_at")
    settled = bets[bets["status"].isin(["gagne", "perdu", "annule"])]
    staked = settled.loc[settled["status"] != "annule", "stake"].sum()
    pnl = settled["pnl"].sum()
    curve = initial + settled["pnl"].cumsum()
    peaks = np.maximum.accumulate(np.r_[initial, curve.to_numpy()])
    dd = float(np.max((peaks - np.r_[initial, curve.to_numpy()]) / peaks)) if len(settled) else 0.0
    return {
        "n_paris": int(len(bets)), "n_regles": int(len(settled)),
        "en_cours": float(bets.loc[bets["status"] == "ouvert", "stake"].sum()),
        "mise_totale": float(staked), "benefice": float(pnl),
        "yield": float(pnl / staked) if staked else np.nan,
        "bankroll": float(initial + pnl), "drawdown_max": dd,
        "clv_moyenne": float(bets["clv"].mean()) if bets["clv"].notna().any() else np.nan,
        "n_clv": int(bets["clv"].notna().sum()),
        "bets": bets,
    }
