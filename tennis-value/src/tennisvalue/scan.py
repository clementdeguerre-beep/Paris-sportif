"""Scanner des matchs à venir : mes cotes, cote juste, verdict sur les cotes françaises, facteurs.

Entrées : derniers relevés de cotes (odds_snapshots) + modèle final (live_model.pkl).
Sortie : une ligne par match, avec :
- p_model    : chance du joueur 1 selon mon modèle (Elo, surface, forme, tournoi, fatigue,
               blessure, mental, face-à-face) ; « ma cote » = 1 / chance ;
- p_sharp    : chance sans marge selon Pinnacle / Betfair Exchange (marché « sharp ») ;
- p_fr       : chance sans marge selon les bookmakers français (moyenne) ;
- p_decision : chance retenue pour juger les cotes (réglage decision_source, sharp par défaut :
               sur les saisons testées, le marché sharp prédit mieux que le modèle) ;
- verdict_1/2 : « haute » quand la meilleure cote française paie plus que la cote juste
               (value au-dessus du seuil), « basse » quand les bookmakers français
               surestiment le joueur d'au moins trap_gap_pp points (piège) ;
- les effets des familles de facteurs (models/explain.py) et la fiche des deux joueurs.
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
from tennisvalue.market.flux import is_french
from tennisvalue.market.staking import StakingRules, expected_value, recommended_stake
from tennisvalue.models.explain import GROUPS, effects
from tennisvalue.models.features import FEATURE_VERSION, FEATURES, add_derived
from tennisvalue.models.markov import final_set_rule
from tennisvalue.models.profile import live_tournament_key


class OutdatedModel(FileNotFoundError):
    """Modèle calculé par une ancienne version (variables différentes) : à recalculer."""


def load_live(settings: Settings) -> dict:
    path = settings.models_dir / "live_model.pkl"
    if not path.exists():
        raise FileNotFoundError("Modèle absent : lancer d'abord `tennisvalue build`.")
    with open(path, "rb") as fh:
        live = pickle.load(fh)
    if live.get("feature_version") != FEATURE_VERSION:
        raise OutdatedModel("Modèle d'une ancienne version : le recalculer (`tennisvalue build`).")
    return live


def key_factors(eff: dict, names: tuple[str, str], n: int = 3) -> str:
    """Les familles de facteurs qui pèsent le plus, en clair (pour la ligne de commande)."""
    top = sorted(eff.items(), key=lambda kv: -abs(kv[1]))[:n]
    parts = [f"{GROUPS[k][0]} → {names[0] if v > 0 else names[1]} ({100 * abs(v):.1f} pts)" for k, v in top if abs(v) >= 0.01]
    return " ; ".join(parts) or "aucun facteur marqué"


def player_card(st, key: str, d, surface: str, tkey: str | None, rank) -> dict:
    """Fiche d'avant-match d'un joueur : chiffres bruts, pour comparer points forts et points faibles."""
    s = st.prof.stats(key, d, surface, tkey)
    fat = st.hist.fatigue(key, d)
    return {
        "elo": st.elo.rating[key], "elo_surf": st.elo.surf_rating[(key, surface)], "rank": rank,
        "rank_3m": s["rank_3m"], "matches": st.elo.count[key],
        "form10": s["form10"], "w10": s["w10"], "n10": s["n10"], "surplus10": s["surplus10"],
        "surf_w": s["surf_w"], "surf_n": s["surf_n"], "tourn_w": s["tourn_w"], "tourn_n": s["tourn_n"],
        "tb_w": s["tb_w"], "tb_l": s["tb_l"], "dec_w": s["dec_w"], "dec_l": s["dec_l"],
        "sets_w": s["sets_w"], "sets_l": s["sets_l"], "lost_first": s["lost_first"], "comebacks": s["won_after_lost_first"],
        "games_7d": fat["games_7d"], "matches_14d": fat["matches_14d"], "days_since": fat["days_since"],
        "last_games": s["last_games"], "m4": s["m4"], "since_break": s["since_break"], "break_days": s["break_days"],
        "inj_days": s["inj_days"], "inj_365": s["inj_365"],
    }


def latest_snapshots(con) -> pd.DataFrame:
    return db.read(con, """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY event_id, book ORDER BY fetched_at DESC) AS rn
            FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP - INTERVAL 3 HOUR
        ) WHERE rn = 1
    """)


def _mean_fair(books: pd.DataFrame) -> float:
    ps = [fair_two_way(a, b)[0] for a, b in zip(books["odds_1"], books["odds_2"])]
    ps = [p for p in ps if pd.notna(p)]
    return float(np.mean(ps)) if ps else np.nan


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
    known_t = {tour: {k for _, k in st.prof.tourn} for tour, st in live["states"].items()}
    rows = []
    for ev_id, g in snaps.groupby("event_id"):
        first = g.iloc[0]
        ctx = tournament_context(first["sport_key"])
        st = live["states"].get(ctx["tour"])               # circuit absent de l'historique : pas de modèle
        known = set(st.elo.count.keys()) if st else set()
        counts = st.elo.count if st else None
        k1, k2 = match_player(first["player_1"], known, counts), match_player(first["player_2"], known, counts)
        tkey = live_tournament_key(first["sport_key"], ctx["surface"], ctx["tier"], known_t.get(ctx["tour"], set()))
        row = {"event_id": ev_id, "commence_time": first["commence_time"], "tour": ctx["tour"],
               "tournoi": first["sport_key"], "surface": ctx["surface"], "tournoi_connu": tkey is not None,
               "joueur_1": first["player_1"], "joueur_2": first["player_2"], "alerte_donnees": ""}
        if not ctx["surface_known"]:
            row["alerte_donnees"] += "surface supposée (dur) ; "

        sharp = g[g["book"].isin(settings.sharp_books)]
        anj = g[[is_french(b, settings.anj_books) for b in g["book"]]]
        row["p_sharp"], row["p_fr"] = _mean_fair(sharp), _mean_fair(anj)

        # Mon modèle : chance, effets des familles de facteurs, fiches des joueurs
        row.update({"p_model": np.nan, "facteurs": None, "facteurs_cles": "", "profil_1": None, "profil_2": None,
                    "h2h": None, "historique_ok": False})
        if k1 and k2:
            rule = final_set_rule(ctx["tier"], now.year, first["sport_key"].replace("_", " "))
            d = now.date()
            r1, r2 = live["last_rank"].get((ctx["tour"], k1), np.nan), live["last_rank"].get((ctx["tour"], k2), np.nan)
            pre = st.pre_match(k1, k2, ctx["tour"], ctx["surface"], ctx["best_of"], rule, d, tkey)
            pre.update({"best_of": ctx["best_of"], "rank_1": r1, "rank_2": r2})
            x = add_derived(pd.DataFrame([pre]))[FEATURES].to_numpy(float)
            model = live["models"][ctx["tour"]]
            eff = {k: float(v[0]) for k, v in effects(model, x).items()}
            row["p_model"] = 0.5 * (model.predict_proba(x)[0, 1] + 1 - model.predict_proba(-x)[0, 1])
            row["facteurs"] = eff
            row["facteurs_cles"] = key_factors(eff, (first["player_1"], first["player_2"]))
            row["profil_1"] = player_card(st, k1, d, ctx["surface"], tkey, r1)
            row["profil_2"] = player_card(st, k2, d, ctx["surface"], tkey, r2)
            row["h2h"] = [pre["h2h_1"], pre["h2h_2"]]
            row["historique_ok"] = min(pre["n_1"], pre["n_2"]) >= 20
            if not row["historique_ok"]:
                row["alerte_donnees"] += "historique < 20 matchs (proba fragile) ; "
        else:
            row["alerte_donnees"] += "joueur absent de l'historique ; "

        p_dec = row["p_model"] if settings.decision_source == "model" else row["p_sharp"]
        row["p_decision"] = p_dec
        if pd.isna(row["p_sharp"]):
            row["alerte_donnees"] += "pas de cote Pinnacle / Betfair ; "

        # Par joueur : meilleure cote française, ma cote, cote juste, value, écart des français, verdict
        for side in (1, 2):
            o, book = np.nan, None
            if not anj.empty:
                best = anj.loc[anj[f"odds_{side}"].idxmax()]
                o, book = best[f"odds_{side}"], best["book"]
            pm = row["p_model"] if side == 1 else 1 - row["p_model"]
            pd_ = p_dec if side == 1 else 1 - p_dec
            pf = row["p_fr"] if side == 1 else 1 - row["p_fr"]
            ev = expected_value(pd_, o) if pd.notna(pd_) and pd.notna(o) else np.nan
            if settings.decision_source == "confirmed" and pd.notna(ev) and pd.notna(pm):   # value exigée aussi selon le modèle
                ev = min(ev, expected_value(pm, o))
            shade = 100 * (pf - pd_) if pd.notna(pf) and pd.notna(pd_) else np.nan
            in_range = pd.notna(o) and settings.min_odds <= o <= settings.max_odds
            verdict = None
            if pd.notna(ev) and ev >= settings.min_ev and in_range:
                verdict = "haute"
            elif pd.notna(shade) and shade >= settings.trap_gap_pp:
                verdict = "basse"
            row.update({f"cote_anj_{side}": o, f"book_{side}": book, f"ma_cote_{side}": 1 / pm if pm and pm > 0 else np.nan,
                        f"cote_juste_{side}": 1 / pd_ if pd.notna(pd_) and pd_ > 0 else np.nan,
                        f"value_{side}": ev, f"ecart_fr_{side}": shade, f"verdict_{side}": verdict,
                        f"modele_ok_{side}": bool(pm * o - 1 > 0) if pd.notna(pm) and pd.notna(o) else None})
        row["ecart_modele"] = 100 * (row["p_model"] - p_dec) if pd.notna(row["p_model"]) and pd.notna(p_dec) else np.nan

        # Pari conseillé : le côté de plus forte value, si elle dépasse le seuil
        row.update({"pari": None, "cote": np.nan, "book": None, "value": np.nan, "mise": 0.0, "statut": "pas de value"})
        sides = [s for s in (1, 2) if pd.notna(row[f"value_{s}"]) and settings.min_odds <= row[f"cote_anj_{s}"] <= settings.max_odds]
        if sides:
            s = max(sides, key=lambda k: row[f"value_{k}"])
            o, ev = row[f"cote_anj_{s}"], row[f"value_{s}"]
            row.update({"pari": row[f"joueur_{s}"], "cote": o, "book": row[f"book_{s}"], "value": ev})
            if ev >= settings.min_ev:
                p_side = p_dec if s == 1 else 1 - p_dec
                row["mise"] = recommended_stake(p_side, o, settings.bankroll, rules)
                row["statut"] = "VALUE"
        rows.append(row)
    out = pd.DataFrame(rows).sort_values(["statut", "value"], ascending=[True, False])
    return out.reset_index(drop=True)
