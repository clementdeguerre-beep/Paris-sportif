"""Scanner des matchs à venir : probabilités, value, mise conseillée, facteurs clés.

Entrées : derniers relevés de cotes (odds_snapshots) + modèle final (live_model.pkl).
Sortie : une ligne par match, avec :
- p_model  : proba du modèle (Elo, points, classement, fatigue…) ;
- p_sharp  : proba sans marge du marché sharp (Pinnacle / Betfair Exchange) ;
- p_decision : proba utilisée pour décider (réglage decision_source) ;
- la meilleure cote ANJ disponible pour chaque joueur et la value correspondante ;
- la mise conseillée (Kelly fractionné) et les facteurs qui pèsent le plus.
"""
from __future__ import annotations

import pickle
from datetime import datetime

import numpy as np
import pandas as pd

from tennisvalue import db
from tennisvalue.config import Settings
from tennisvalue.ingest.oddsapi import match_player, tournament_context
from tennisvalue.market.devig import fair_two_way
from tennisvalue.market.staking import StakingRules, expected_value, recommended_stake
from tennisvalue.models.features import FEATURES, add_derived
from tennisvalue.models.markov import final_set_rule

FACTOR_LABELS = {
    "x_logit_elo": "Elo global",
    "x_logit_elo_surf": "Elo sur la surface",
    "x_logit_pts": "Domination aux points",
    "x_log_rank_ratio": "Classement officiel",
    "x_exp": "Expérience (matchs joués)",
    "x_games14": "Activité récente (jeux joués sur 14 j)",
    "x_games7": "Charge des 7 derniers jours",
    "x_rust": "Inactivité (jours sans jouer)",
    "x_h2h": "Confrontations directes",
    "x_bo5_elo": "Format 3 sets gagnants",
}


def load_live(settings: Settings) -> dict:
    path = settings.models_dir / "live_model.pkl"
    if not path.exists():
        raise FileNotFoundError("Modèle absent : lancer d'abord `tennisvalue build`.")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def key_factors(model, x: np.ndarray, n: int = 3) -> str:
    """Contribution de chaque facteur au logit (coefficient x valeur standardisée)."""
    scaler = model.named_steps["standardscaler"]
    coef = model.named_steps["logisticregression"].coef_[0]
    contrib = coef * x / scaler.scale_
    order = np.argsort(-np.abs(contrib))[:n]
    parts = []
    for i in order:
        if abs(contrib[i]) < 0.05:
            continue
        who = "J1" if contrib[i] > 0 else "J2"
        parts.append(f"{FACTOR_LABELS[FEATURES[i]]} → {who} ({contrib[i]:+.2f})")
    return " ; ".join(parts) or "aucun facteur marqué"


def latest_snapshots(con) -> pd.DataFrame:
    return db.read(con, """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY event_id, book ORDER BY fetched_at DESC) AS rn
            FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP - INTERVAL 3 HOUR
        ) WHERE rn = 1
    """)


def scan(settings: Settings, snaps: pd.DataFrame | None = None, now: datetime | None = None,
         live: dict | None = None) -> pd.DataFrame:
    live = live or load_live(settings)
    con = db.connect(settings.db_path)
    snaps = latest_snapshots(con) if snaps is None else snaps
    con.close()
    if snaps.empty:
        return pd.DataFrame()
    now = now or datetime.now()
    rules = StakingRules(settings.kelly_multiplier, settings.max_stake_pct, settings.max_exposure_pct)
    rows = []
    for ev_id, g in snaps.groupby("event_id"):
        first = g.iloc[0]
        ctx = tournament_context(first["sport_key"])
        st = live["states"][ctx["tour"]]
        known = set(st.elo.count.keys())
        k1, k2 = match_player(first["player_1"], known), match_player(first["player_2"], known)
        row = {"event_id": ev_id, "commence_time": first["commence_time"], "tour": ctx["tour"],
               "tournoi": first["sport_key"], "surface": ctx["surface"],
               "joueur_1": first["player_1"], "joueur_2": first["player_2"],
               "alerte_donnees": ""}
        if not ctx["surface_known"]:
            row["alerte_donnees"] += "surface supposée (dur) ; "

        # Proba du marché sharp
        sharp = g[g["book"].isin(settings.sharp_books)]
        ps = [fair_two_way(a, b)[0] for a, b in zip(sharp["odds_1"], sharp["odds_2"])]
        ps = [p for p in ps if pd.notna(p)]
        row["p_sharp"] = float(np.mean(ps)) if ps else np.nan

        # Proba du modèle
        if k1 and k2:
            rule = final_set_rule(ctx["tier"], now.year, first["sport_key"].replace("_", " "))
            pre = st.pre_match(k1, k2, ctx["tour"], ctx["surface"], ctx["best_of"], rule, now.date())
            pre.update({"best_of": ctx["best_of"],
                        "rank_1": live["last_rank"].get((ctx["tour"], k1), np.nan),
                        "rank_2": live["last_rank"].get((ctx["tour"], k2), np.nan)})
            x = add_derived(pd.DataFrame([pre]))[FEATURES].to_numpy(float)
            model = live["models"][ctx["tour"]]
            p = model.predict_proba(x)[0, 1]
            p_rev = model.predict_proba(-x)[0, 1]
            row["p_model"] = 0.5 * (p + 1 - p_rev)
            row["facteurs_cles"] = key_factors(model, x[0])
            if min(pre["n_1"], pre["n_2"]) < 20:
                row["alerte_donnees"] += "historique < 20 matchs (proba fragile) ; "
        else:
            row["p_model"] = np.nan
            row["facteurs_cles"] = ""
            row["alerte_donnees"] += "joueur absent de l'historique ; "

        # Proba de décision
        if settings.decision_source == "model":
            p_dec = row["p_model"]
        else:
            p_dec = row["p_sharp"]
        row["p_decision"] = p_dec

        # Meilleure cote ANJ de chaque côté
        anj = g[g["book"].isin(settings.anj_books)]
        for side in (1, 2):
            if anj.empty:
                row[f"cote_anj_{side}"], row[f"book_{side}"] = np.nan, None
                continue
            best = anj.loc[anj[f"odds_{side}"].idxmax()]
            row[f"cote_anj_{side}"], row[f"book_{side}"] = best[f"odds_{side}"], best["book"]

        # Value et mise
        row.update({"pari": None, "cote": np.nan, "book": None, "value": np.nan, "mise": 0.0, "statut": "pas de value"})
        if pd.notna(p_dec):
            best_side, best_ev = None, -1.0
            for side in (1, 2):
                o = row[f"cote_anj_{side}"]
                if pd.isna(o) or not (settings.min_odds <= o <= settings.max_odds):
                    continue
                p_side = p_dec if side == 1 else 1 - p_dec
                ev = expected_value(p_side, o)
                if settings.decision_source == "confirmed" and pd.notna(row["p_model"]):
                    pm = row["p_model"] if side == 1 else 1 - row["p_model"]
                    ev = min(ev, expected_value(pm, o))
                if ev > best_ev:
                    best_side, best_ev = side, ev
            if best_side is not None:
                o = row[f"cote_anj_{best_side}"]
                p_side = p_dec if best_side == 1 else 1 - p_dec
                row.update({"pari": row[f"joueur_{best_side}"], "cote": o, "book": row[f"book_{best_side}"],
                            "value": best_ev})
                if best_ev >= settings.min_ev:
                    row["mise"] = recommended_stake(p_side, o, settings.bankroll, rules)
                    row["statut"] = "VALUE"
        elif pd.isna(row["p_sharp"]):
            row["alerte_donnees"] += "pas de cote sharp ; "
        rows.append(row)
    out = pd.DataFrame(rows).sort_values(["statut", "value"], ascending=[True, False])
    return out.reset_index(drop=True)
