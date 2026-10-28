"""Chaîne complète : ingestion -> variables -> modèles walk-forward -> backtest -> sauvegarde."""
from __future__ import annotations

import json
import pickle
from datetime import datetime

import numpy as np
import pandas as pd

from tennisvalue import db
from tennisvalue.backtest import metrics
from tennisvalue.backtest import run as bt
from tennisvalue.config import Settings
from tennisvalue.ingest.tennisdata import load_all
from tennisvalue.market.devig import fair_two_way
from tennisvalue.models import ensemble
from tennisvalue.models.explain import GROUPS, effects
from tennisvalue.models.features import FEATURE_VERSION, FEATURES, build_features

TEST_SEASONS = list(range(2019, 2027))


def market_probs(f: pd.DataFrame) -> pd.DataFrame:
    """Probabilités sans marge (méthode power) pour chaque bookmaker + référence sharp."""
    f = f.copy()
    for bk in ("ps", "bfe", "b365", "avg", "max"):
        f[f"p_{bk}"] = [
            fair_two_way(a, b)[0] if pd.notna(a) and pd.notna(b) else np.nan
            for a, b in zip(f[f"odds_{bk}_1"], f[f"odds_{bk}_2"])
        ]
    # Référence sharp : Pinnacle ; Betfair Exchange quand Pinnacle manque (2026).
    f["p_sharp"] = f["p_ps"].where(f["p_ps"].notna(), f["p_bfe"])
    f["sharp_source"] = np.where(f["p_ps"].notna(), "Pinnacle", np.where(f["p_bfe"].notna(), "Betfair Exchange", None))
    return f


def build(settings: Settings, log=print) -> pd.DataFrame:
    log("1/4 Lecture des fichiers tennis-data…")
    matches = load_all(settings.raw_dir)
    log(f"    {len(matches):,} matchs ({matches.date.min()} -> {matches.date.max()})")
    log("2/4 Calcul des notes et variables d'avant-match (ordre chronologique)…")
    feats, states = build_features(matches)
    log("3/4 Modèles d'ensemble en walk-forward (logit + gradient boosting)…")
    feats["p_logit"] = ensemble.walk_forward(feats, TEST_SEASONS, "logit")
    feats["p_gbm"] = ensemble.walk_forward(feats, TEST_SEASONS, "gbm")
    feats = market_probs(feats)

    log("4/4 Modèles finaux (toutes saisons) pour le scanner…")
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    final = {}
    for tour, g in feats.groupby("tour"):
        train = g[(g.season >= ensemble.TRAIN_START) & g.completed]
        final[tour] = ensemble.fit(train, "logit")
    last_rank = _last_ranks(matches)
    with open(settings.models_dir / "live_model.pkl", "wb") as fh:
        pickle.dump({"models": final, "states": states, "last_rank": last_rank, "feature_version": FEATURE_VERSION,
                     "data_until": str(matches.date.max()), "built_at": datetime.now().isoformat()}, fh)

    con = db.connect(settings.db_path)
    db.write_table(con, "matches", matches.drop(columns=["file_row"]))
    db.write_table(con, "features", feats)
    con.close()
    return feats


def factor_weights(feats: pd.DataFrame, models: dict) -> pd.DataFrame:
    """Poids de chaque famille de facteurs dans le modèle final : effet moyen (en points de %)
    sur les matchs de la dernière saison, et part des matchs où elle pèse au moins 3 points."""
    rows = []
    for tour, model in models.items():
        g = feats[(feats.tour == tour) & (feats.season == feats.season.max()) & ~feats.walkover]
        eff = effects(model, g[FEATURES].to_numpy(float))
        for key, v in eff.items():
            rows.append({"tour": tour, "famille": key, "nom": GROUPS[key][0], "effet_moyen_pts": 100 * np.abs(v).mean(),
                         "part_3pts": float((np.abs(v) >= 0.03).mean())})
    return pd.DataFrame(rows)


def public_bias(feats: pd.DataFrame) -> pd.DataFrame:
    """Les bookmakers « grand public » (Bet365) face à Pinnacle, selon le classement du mieux classé.

    ecart_pts : chance du mieux classé selon Bet365 - selon Pinnacle (sans marge) ;
    yield_* : rendement d'un euro misé à la cote Bet365 sur le mieux classé, ou sur son adversaire.
    """
    d = feats[feats.completed & feats.p_ps.notna() & feats.p_b365.notna() & feats.season.isin(TEST_SEASONS)]
    b1 = d.rank_1.fillna(9999) <= d.rank_2.fillna(9999)
    x = pd.DataFrame({
        "tour": d.tour, "rank": np.where(b1, d.rank_1, d.rank_2), "y": np.where(b1, d.y, 1 - d.y),
        "p365": np.where(b1, d.p_b365, 1 - d.p_b365), "pps": np.where(b1, d.p_ps, 1 - d.p_ps),
        "o_fav": np.where(b1, d.odds_b365_1, d.odds_b365_2), "o_out": np.where(b1, d.odds_b365_2, d.odds_b365_1),
    })
    x["tranche"] = pd.cut(x["rank"], [0, 5, 10, 20, 50, 100, 100000],
                          labels=["top 5", "6-10", "11-20", "21-50", "51-100", "> 100"])
    rows = []
    for (tour, tr), g in x.groupby(["tour", "tranche"], observed=True):
        rows.append({"tour": tour, "tranche": tr, "n": len(g), "ecart_pts": 100 * (g.p365 - g.pps).mean(),
                     "chance_reelle": g.y.mean(), "chance_pinnacle": g.pps.mean(),
                     "yield_mieux_classe": float(np.where(g.y == 1, g.o_fav - 1, -1).mean()),
                     "yield_adversaire": float(np.where(g.y == 0, g.o_out - 1, -1).mean())})
    return pd.DataFrame(rows)


def _last_ranks(m: pd.DataFrame) -> dict:
    w = m[["tour", "date", "seq", "winner_key", "w_rank"]].rename(columns={"winner_key": "key", "w_rank": "rank"})
    l = m[["tour", "date", "seq", "loser_key", "l_rank"]].rename(columns={"loser_key": "key", "l_rank": "rank"})
    a = pd.concat([w, l]).dropna(subset=["rank"]).sort_values("seq")
    return {(r.tour, r.key): float(r.rank) for r in a.groupby(["tour", "key"]).last().reset_index().itertuples()}


def evaluate(feats: pd.DataFrame) -> dict:
    """Qualité des probabilités : modèles vs marché, sur les mêmes matchs."""
    ev = feats[feats.completed & feats.season.isin(TEST_SEASONS) & feats.p_logit.notna()
               & feats.p_sharp.notna() & feats.p_avg.notna()]
    rows = []
    labels = {
        "p_elo": "Elo global", "p_elo_blend": "Elo global + surface", "p_pts": "Note aux points (Markov)",
        "p_logit": "Ensemble (régression logistique)", "p_gbm": "Ensemble (gradient boosting)",
        "p_sharp": "Marché sharp (Pinnacle / Betfair)", "p_avg": "Marché, cote moyenne",
        "p_b365": "Bet365",
    }
    for tour, g in ev.groupby("tour"):
        for col, label in labels.items():
            h = g[g[col].notna()]
            rows.append({"tour": tour, "source": label, "n": len(h),
                         "log_loss": metrics.log_loss(h[col], h.y), "brier": metrics.brier(h[col], h.y),
                         "precision": metrics.accuracy(h[col], h.y)})
    calib = {
        "modele": metrics.calibration_table(ev["p_logit"], ev["y"]),
        "marche": metrics.calibration_table(ev["p_sharp"], ev["y"]),
    }
    return {"scores": pd.DataFrame(rows), "calibration": calib, "n_matchs": len(ev),
            "periode": (str(ev.date.min()), str(ev.date.max()))}


def information_test(feats: pd.DataFrame) -> pd.DataFrame:
    """Le modèle apporte-t-il une information que le marché n'a pas ?

    Pour chaque saison S, régression logistique sur les saisons précédentes :
    logit(résultat) ~ a x logit(proba marché) + b x logit(proba modèle).
    Si b ~ 0 (ou < 0), le modèle n'ajoute rien au marché.
    """
    from sklearn.linear_model import LogisticRegression

    def lg(p):
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return np.log(p / (1 - p))

    d = feats[feats.completed & feats.p_logit.notna() & feats.p_avg.notna()]
    out = []
    for tour, g in d.groupby("tour"):
        for s in TEST_SEASONS[1:]:
            tr, te = g[g.season < s], g[g.season == s]
            if te.empty:
                continue
            X = np.c_[lg(tr.p_avg), lg(tr.p_logit)]
            y = tr.y.to_numpy()
            lr = LogisticRegression(fit_intercept=False, C=100).fit(np.r_[X, -X], np.r_[y, 1 - y])
            p = lr.predict_proba(np.c_[lg(te.p_avg), lg(te.p_logit)])[:, 1]
            out.append({"tour": tour, "saison": s, "n": len(te),
                        "log_loss_marche": metrics.log_loss(te.p_avg, te.y),
                        "log_loss_marche_plus_modele": metrics.log_loss(p, te.y),
                        "poids_marche": lr.coef_[0][0], "poids_modele": lr.coef_[0][1]})
    return pd.DataFrame(out)


def backtest(feats: pd.DataFrame, settings: Settings, log=print) -> dict:
    log("Backtest des stratégies…")
    f = feats.copy()
    f["p_model"] = f["p_logit"]
    res = bt.run(f[f.season.isin(TEST_SEASONS)])
    focus = {
        "modele_b365_5": ("modele", "b365", 0.05),
        "modele_anj_5": ("modele", "anj_proxy", 0.05),
        "confirme_b365_2": ("modele_confirme", "b365", 0.02),
        "sharp_b365_2": ("sharp", "b365", 0.02),
        "sharp_anj_2": ("sharp", "anj_proxy", 0.02),
    }
    cfg = bt.BacktestConfig()
    details = {}
    for name, key in focus.items():
        bets = res["bets"].get(key)
        if bets is None or bets.empty:
            continue
        prob_col = "p_sharp" if key[0] == "sharp" else "p_model"
        details[name] = {"breakdown": bt.breakdown(bets), "kelly": bt.kelly_report(bets, prob_col, cfg),
                         "bets": bets}
    con = db.connect(settings.db_path)
    db.write_table(con, "bt_summary", res["summary"])
    con.close()
    return {"summary": res["summary"], "details": details}


def build_all(settings: Settings, log=print) -> dict:
    """`tennisvalue build` : données, modèles, évaluation, backtest, puis rapports dans reports/."""
    feats = build(settings, log)
    ev = evaluate(feats)
    info = information_test(feats)
    bt = backtest(feats, settings, log)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.models_dir / "live_model.pkl", "rb") as fh:
        models = pickle.load(fh)["models"]
    payload = {
        "genere_le": datetime.now().isoformat(timespec="minutes"),
        "evaluation": ev, "test_information": info, "backtest": bt["summary"],
        "poids_facteurs": factor_weights(feats, models), "biais_public": public_bias(feats),
        "details": {k: {"breakdown": v["breakdown"],
                        "kelly": {kk: vv for kk, vv in v["kelly"].items()}} for k, v in bt["details"].items()},
    }
    (settings.reports_dir / "backtest.json").write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=1))
    with open(settings.reports_dir / "backtest_details.pkl", "wb") as fh:
        pickle.dump(bt["details"], fh)
    return {"evaluation": ev, "backtest": bt}


def to_jsonable(obj):
    if isinstance(obj, pd.DataFrame):
        return json.loads(obj.to_json(orient="records", date_format="iso"))
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj
