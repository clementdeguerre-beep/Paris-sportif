"""Backtest walk-forward des stratégies de value betting.

Principe : chaque probabilité utilisée pour décider d'un pari est calculée
uniquement avec l'information disponible avant le match (notes en ligne +
modèle d'ensemble réentraîné saison par saison). Les cotes viennent de
tennis-data (une seule cote d'avant-match par bookmaker).

Conventions :
- un pari maximum par match (le côté avec la plus forte value) ;
- matchs abandonnés et forfaits : pari annulé (mise rendue), donc exclus.
  Attention : la règle varie selon les bookmakers (chez certains, le pari est
  réglé si un set est terminé) ;
- mises à plat de 1 unité pour les statistiques (yield, IC 95 %) ;
- simulation de bankroll séparée en Kelly fractionné.

Stratégies (cote prise chez le bookmaker « book ») :
- modele          : value selon notre modèle >= seuil ;
- modele_confirme : value selon notre modèle >= seuil ET la cote reste au-dessus
                    de la cote « juste » du marché sharp (Pinnacle, ou Betfair
                    Exchange quand Pinnacle manque) ;
- sharp           : sans modèle, value selon la proba sharp >= seuil (cote
                    « en retard » sur le marché de référence).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tennisvalue.backtest.metrics import betting_summary, max_drawdown, odds_bucket
from tennisvalue.market.staking import StakingRules, recommended_stake, scale_to_exposure

BOOK_LABELS = {
    "b365": "Bet365",
    "avg": "Cote moyenne du marché",
    "max": "Meilleure cote du marché (optimiste)",
    "ps": "Pinnacle",
    "anj_proxy": "Proxy ANJ (Bet365 + 3,3 pts de marge)",
}


@dataclass(frozen=True)
class BacktestConfig:
    min_odds: float = 1.30
    max_odds: float = 5.00
    thresholds: tuple = (0.02, 0.03, 0.05, 0.10)
    books: tuple = ("b365", "avg", "anj_proxy", "max", "ps")
    strategies: tuple = ("modele", "modele_confirme", "sharp")
    anj_extra_margin: float = 0.033  # source : OddsPapi, sept. 2026 (versions .fr vs autres versions des mêmes marques)
    initial_bankroll: float = 1000.0


def add_anj_proxy(df: pd.DataFrame, extra: float) -> pd.DataFrame:
    """Scénario : cotes Bet365 dégradées pour simuler la marge plus élevée des sites français."""
    df = df.copy()
    o1, o2 = df["odds_b365_1"], df["odds_b365_2"]
    m = 1 / o1 + 1 / o2
    k = (m + extra) / m
    df["odds_anj_proxy_1"] = o1 / k
    df["odds_anj_proxy_2"] = o2 / k
    return df


def candidate_bets(df: pd.DataFrame, book: str, strategy: str, thr: float, cfg: BacktestConfig) -> pd.DataFrame:
    """Sélectionne les paris d'une stratégie (au plus un côté par match)."""
    d = df[df.completed & df.p_model.notna()].copy()
    o1, o2 = d[f"odds_{book}_1"], d[f"odds_{book}_2"]
    pm1, ps1 = d["p_model"], d["p_sharp"]
    ev_m1, ev_m2 = pm1 * o1 - 1, (1 - pm1) * o2 - 1
    ev_s1, ev_s2 = ps1 * o1 - 1, (1 - ps1) * o2 - 1
    if strategy == "modele":
        ok1, ok2 = ev_m1 >= thr, ev_m2 >= thr
        score1, score2 = ev_m1, ev_m2
    elif strategy == "modele_confirme":
        ok1, ok2 = (ev_m1 >= thr) & (ev_s1 > 0), (ev_m2 >= thr) & (ev_s2 > 0)
        score1, score2 = ev_m1, ev_m2
    elif strategy == "sharp":
        if book == "ps":
            return pd.DataFrame()
        ok1, ok2 = ev_s1 >= thr, ev_s2 >= thr
        score1, score2 = ev_s1, ev_s2
    else:
        raise ValueError(strategy)
    in1 = o1.between(cfg.min_odds, cfg.max_odds)
    in2 = o2.between(cfg.min_odds, cfg.max_odds)
    ok1, ok2 = ok1 & in1, ok2 & in2
    pick1 = ok1 & (~ok2 | (score1 >= score2))
    pick2 = ok2 & ~pick1
    rows = []
    for side, pick in ((1, pick1), (2, pick2)):
        s = d[pick]
        if s.empty:
            continue
        won = (s["y"] == 1) if side == 1 else (s["y"] == 0)
        odds = s[f"odds_{book}_{side}"]
        p_mod = s["p_model"] if side == 1 else 1 - s["p_model"]
        p_sh = s["p_sharp"] if side == 1 else 1 - s["p_sharp"]
        rows.append(pd.DataFrame({
            "match_id": s["match_id"], "date": s["date"], "season": s["season"], "tour": s["tour"],
            "tournament": s["tournament"], "tier": s["tier"], "surface": s["surface"],
            "selection": s["p1"] if side == 1 else s["p2"],
            "opponent": s["p2"] if side == 1 else s["p1"],
            "odds": odds, "p_model": p_mod, "p_sharp": p_sh,
            "ev_model": p_mod * odds - 1, "ev_sharp": p_sh * odds - 1,
            "won": won.astype(int),
        }))
    if not rows:
        return pd.DataFrame()
    bets = pd.concat(rows).sort_values(["date", "match_id"]).reset_index(drop=True)
    bets["ret"] = np.where(bets["won"] == 1, bets["odds"] - 1, -1.0)
    bets["book"], bets["strategy"], bets["threshold"] = book, strategy, thr
    return bets


def simulate_kelly(bets: pd.DataFrame, prob_col: str, cfg: BacktestConfig,
                   rules: StakingRules = StakingRules()) -> pd.DataFrame:
    """Bankroll en Kelly fractionné, jour par jour (mises du jour plafonnées ensemble)."""
    bank = cfg.initial_bankroll
    out = []
    for d, day in bets.groupby("date", sort=True):
        stakes = [recommended_stake(p, o, bank, rules) for p, o in zip(day[prob_col], day["odds"])]
        stakes = scale_to_exposure(stakes, bank, rules)
        pnl = float(sum(s * r for s, r in zip(stakes, day["ret"])))
        bank += pnl
        out.append({"date": d, "mise": sum(stakes), "pnl": pnl, "bankroll": bank})
    return pd.DataFrame(out)


def run(df: pd.DataFrame, cfg: BacktestConfig = BacktestConfig()) -> dict:
    df = add_anj_proxy(df, cfg.anj_extra_margin)
    summary, all_bets = [], {}
    for strategy in cfg.strategies:
        for book in cfg.books:
            for thr in cfg.thresholds:
                bets = candidate_bets(df, book, strategy, thr, cfg)
                if bets.empty:
                    continue
                s = betting_summary(bets["ret"].to_numpy(), bets["odds"].to_numpy())
                has_sharp = bets["p_sharp"].notna()
                s.update({
                    "strategie": strategy, "book": book, "seuil": thr,
                    "clv_proxy": float(bets.loc[has_sharp, "ev_sharp"].mean()) if has_sharp.any() else np.nan,
                })
                summary.append(s)
                all_bets[(strategy, book, thr)] = bets
    summary = pd.DataFrame(summary)
    return {"summary": summary, "bets": all_bets}


def breakdown(bets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Réussite par circuit, saison, surface, catégorie de tournoi et tranche de cote."""
    b = bets.copy()
    b["tranche_cote"] = odds_bucket(b["odds"])
    out = {}
    for col in ("tour", "season", "surface", "tier", "tranche_cote"):
        g = b.groupby(col, observed=True).agg(
            n_paris=("ret", "size"), reussite=("won", "mean"), cote_moy=("odds", "mean"),
            benefice_u=("ret", "sum"), yield_=("ret", "mean"),
        ).reset_index()
        out[col] = g.rename(columns={"yield_": "yield"})
    return out


def kelly_report(bets: pd.DataFrame, prob_col: str, cfg: BacktestConfig) -> dict:
    curve = simulate_kelly(bets, prob_col, cfg)
    if curve.empty:
        return {"curve": curve, "final": cfg.initial_bankroll, "max_dd": 0.0}
    equity = np.concatenate([[cfg.initial_bankroll], curve["bankroll"].to_numpy()])
    return {
        "curve": curve,
        "final": float(curve["bankroll"].iloc[-1]),
        "total_mise": float(curve["mise"].sum()),
        "roi_bankroll": float(curve["bankroll"].iloc[-1] / cfg.initial_bankroll - 1),
        "max_dd": max_drawdown(equity),
    }
