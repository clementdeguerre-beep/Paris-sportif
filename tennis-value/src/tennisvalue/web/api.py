"""Application web : API JSON + interface (static/). Lancée par `tennisvalue dashboard`.

Écoute uniquement sur l'ordinateur (127.0.0.1). Les horodatages de la base sont en
UTC ; l'API les renvoie au format ISO avec « Z », l'interface les affiche à l'heure
de Paris.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tennisvalue import db, sync
from tennisvalue.config import Settings, load_settings, save_settings
from tennisvalue.ingest import polymarket as pm
from tennisvalue.ingest.snapshots import import_snapshots
from tennisvalue.market import flux, money
from tennisvalue.market.devig import fair_two_way
from tennisvalue.market.movement import steam_moves

STATIC = Path(__file__).parent / "static"
BOOK_NAMES = {
    "betclic_fr": "Betclic", "winamax_fr": "Winamax", "unibet_fr": "Unibet", "pmu_fr": "PMU",
    "parionssport_fr": "Parions Sport", "netbet_fr": "NetBet", "zebet_fr": "ZEbet", "bwin_fr": "Bwin",
    "pinnacle": "Pinnacle", "betfair_ex_eu": "Betfair Exchange",
}
EDITABLE = {"bankroll": float, "kelly_multiplier": float, "max_stake_pct": float, "max_exposure_pct": float,
            "min_ev": float, "min_odds": float, "max_odds": float, "decision_source": str}

app = FastAPI(title="Tennis Value", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_lock = threading.Lock()          # relevé, mise à jour et recalcul : un à la fois
_cache: dict = {}
VERSION = sync.code_version()     # comparé par scripts/lancer.sh pour redémarrer après une mise à jour


# --------------------------------------------------------------------------- outils
def book_name(key: str) -> str:
    return BOOK_NAMES.get(key) or key.removesuffix("_fr").removesuffix("_eu").replace("_", " ").title()


def tournament(sport_key: str) -> str:
    words = sport_key.removeprefix("tennis_").split("_")
    return " ".join(w.upper() if w in ("atp", "wta") else w.capitalize() for w in words)


def clean(obj):
    """Valeurs JSON : NaN -> None, horodatages UTC -> ISO « Z », types numpy -> Python."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return None if pd.isna(obj) else pd.Timestamp(obj).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return None if math.isnan(obj) or math.isinf(obj) else float(obj)
    if obj is pd.NaT:
        return None
    return obj


def ok(data) -> JSONResponse:
    return JSONResponse(clean(data))


def live_model(s: Settings):
    path = s.models_dir / "live_model.pkl"
    if not path.exists():
        return None
    key = ("live", str(path), path.stat().st_mtime)
    if _cache.get("live_key") != key:
        from tennisvalue.scan import load_live

        _cache["live"], _cache["live_key"] = load_live(s), key
    return _cache["live"]


def predictions(s: Settings, last_fetch) -> pd.DataFrame:
    """Résultat du scanner, recalculé seulement si les cotes, le modèle ou les réglages changent."""
    live = live_model(s)
    if live is None or last_fetch is None:
        return pd.DataFrame()
    key = (str(last_fetch), _cache.get("live_key"), repr(asdict(s)))
    if _cache.get("pred_key") != key:
        from tennisvalue.scan import scan

        _cache["pred"], _cache["pred_key"] = scan(s, live=live), key
    return _cache["pred"]


def upcoming_snapshots(s: Settings) -> pd.DataFrame:
    con = db.connect(s.db_path)
    snaps = db.read(con, "SELECT * FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP")
    con.close()
    return snaps


def poly_last(s: Settings):
    con = db.connect(s.db_path)
    v = con.execute("SELECT max(fetched_at) FROM poly_markets").fetchall()[0][0]
    con.close()
    return v


def open_stakes(s: Settings) -> float:
    con = db.connect(s.db_path)
    v = con.execute("SELECT coalesce(sum(stake), 0) FROM bets WHERE status = 'ouvert'").fetchone()[0]
    con.close()
    return float(v)


# --------------------------------------------------------------------------- statut
@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/status")
def status():
    s = load_settings()
    stt = sync.status(s)
    live = live_model(s)
    return ok({
        **stt,
        "data_until": live["data_until"] if live else None,
        "can_fetch": "local" if s.odds_api_key else ("github" if sync.gh_ready() else None),
        "settings": {k: getattr(s, k) for k in EDITABLE},
        "open_total": open_stakes(s),
        "poly_last": poly_last(s),
        "min_stake": 1.0,
    })


# --------------------------------------------------------------------------- Polymarket
def poly_data(s: Settings) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Dernier état de chaque match Polymarket récent, ses transactions et son prix horaire."""
    con = db.connect(s.db_path)
    recent = "SELECT cond FROM poly_markets WHERE game_start > now()::TIMESTAMP - INTERVAL 1 DAY"
    mk = db.read(con, f"""SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY cond ORDER BY fetched_at DESC) rn
                          FROM poly_markets WHERE cond IN ({recent})) WHERE rn = 1""")
    tr = db.read(con, f"SELECT * FROM poly_trades WHERE cond IN ({recent})")
    px = db.read(con, f"SELECT * FROM poly_prices WHERE cond IN ({recent}) ORDER BY ts")
    con.close()
    return mk, tr, px


def poly_summary(cond: str, m: pd.Series, trades: pd.DataFrame, swapped: bool) -> dict:
    """Résumé d'un match Polymarket, orienté comme le match des bookmakers (joueur 1 = leur joueur 1)."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    p, p24 = money.pressure(trades), money.pressure(trades, since=now - pd.Timedelta(hours=24))
    flip = (lambda x: 1 - x) if swapped else (lambda x: x)
    return {"cond": cond, "fetched_at": m["fetched_at"], "series": m["series"], "price_1": flip(m["price_1"]),
            "volume": m["volume"], "volume_24h": m["volume_24h"], "liquidity": m["liquidity"],
            "usd_1": p["usd_2"] if swapped else p["usd_1"], "usd_2": p["usd_1"] if swapped else p["usd_2"],
            "share_1": flip(p["share_1"]), "share_1_24h": flip(p24["share_1"]), "usd_24h": p24["usd_1"] + p24["usd_2"],
            "n": p["n"], "n_big": p["n_big"]}


def poly_links(s: Settings, snaps: pd.DataFrame, mk: pd.DataFrame) -> dict:
    events = snaps.drop_duplicates("event_id")[["event_id", "player_1", "player_2", "commence_time"]]
    return money.link(events, mk)


# --------------------------------------------------------------------------- matchs
def _match_entries(s: Settings, snaps: pd.DataFrame, event_ids=None) -> list[dict]:
    if snaps.empty:
        return []
    stt = sync.status(s)
    pred = predictions(s, stt["last_fetch"])
    pred = pred.set_index("event_id") if not pred.empty else pred
    fx = flux.summary(snaps, s.sharp_books, s.anj_books).set_index("event_id")
    fx24 = flux.summary(snaps, s.sharp_books, s.anj_books,
                        since=snaps["fetched_at"].max() - pd.Timedelta(hours=24)).set_index("event_id")
    best = flux.best_odds(snaps, s.anj_books).set_index("event_id")
    steam = steam_moves(snaps, s.sharp_books, s.steam_move_pp, s.steam_window_min)
    steam_ev = set(steam["event_id"]) if not steam.empty else set()
    con = db.connect(s.db_path)
    bets = db.read(con, "SELECT bet_id, event_id, selection, book, odds, stake, status FROM bets WHERE event_id IS NOT NULL")
    con.close()
    mk, tr, _ = poly_data(s)
    links = poly_links(s, snaps, mk)
    mk = mk.set_index("cond")

    latest = snaps.sort_values("fetched_at").groupby(["event_id", "book"]).tail(1)
    out = []
    for ev, g in latest.groupby("event_id"):
        if event_ids is not None and ev not in event_ids:
            continue
        first = g.iloc[0]
        books = []
        for r in g.itertuples():
            p1 = fair_two_way(r.odds_1, r.odds_2)[0]
            books.append({"key": r.book, "name": book_name(r.book), "french": flux.is_french(r.book, s.anj_books),
                          "sharp": r.book in s.sharp_books, "odds_1": r.odds_1, "odds_2": r.odds_2,
                          "margin": 1 / r.odds_1 + 1 / r.odds_2 - 1, "fair_1": p1, "updated": r.fetched_at})
        books.sort(key=lambda b: (not b["french"], not b["sharp"], b["name"]))
        e = {"event_id": ev, "start": first["commence_time"], "sport_key": first["sport_key"],
             "tournament": tournament(first["sport_key"]), "tour": "ATP" if "_atp_" in first["sport_key"] else "WTA",
             "player_1": first["player_1"], "player_2": first["player_2"], "books": books,
             "my_bets": bets[bets["event_id"] == ev].to_dict("records"), "steam": ev in steam_ev}
        if ev in best.index:
            e.update(best.loc[ev].to_dict())
            e["book_1_name"], e["book_2_name"] = book_name(e["book_1"]), book_name(e["book_2"])
        if ev in fx.index:
            e["flux"] = fx.loc[ev].to_dict()
            e["flux_24h"] = fx24.loc[ev].to_dict() if ev in fx24.index else None
        if ev in links:
            cond, swapped = links[ev]
            e["poly"] = poly_summary(cond, mk.loc[cond], tr[tr["cond"] == cond], swapped)
        if not pred.empty and ev in pred.index:
            p = pred.loc[ev]
            e.update({k: p[k] for k in ("p_sharp", "p_model", "p_decision", "pari", "cote", "book", "value", "mise",
                                        "statut", "facteurs_cles", "alerte_donnees", "surface")})
        out.append(e)
    return sorted(out, key=lambda e: (e["start"], e["tournament"]))


@app.get("/api/matches")
def matches():
    s = load_settings()
    return ok(_match_entries(s, upcoming_snapshots(s)))


@app.get("/api/matches/{event_id}")
def match_detail(event_id: str):
    s = load_settings()
    con = db.connect(s.db_path)
    snaps = db.read(con, "SELECT * FROM odds_snapshots WHERE event_id = ? ORDER BY fetched_at", [event_id])
    con.close()
    entries = _match_entries(s, snaps)
    if not entries:
        raise HTTPException(404, "Match inconnu")
    e = entries[0]
    series = []
    for book, g in snaps.groupby("book"):
        series.append({"key": book, "name": book_name(book), "french": flux.is_french(book, s.anj_books),
                       "sharp": book in s.sharp_books,
                       "points": [[t, o1, o2] for t, o1, o2 in zip(g["fetched_at"], g["odds_1"], g["odds_2"])]})
    series.sort(key=lambda b: (not b["french"], not b["sharp"], b["name"]))
    t = flux.timeline(snaps, s.sharp_books, s.anj_books)
    e["series"] = series
    e["consensus"] = [[r.fetched_at, r.fr, r.sharp] for r in t.itertuples()]
    if "poly" in e:
        mk, tr, px = poly_data(s)
        cond = e["poly"]["cond"]
        swapped = poly_links(s, snaps, mk).get(event_id, (cond, False))[1]
        sign = -1 if swapped else 1
        trades = tr[tr["cond"] == cond]
        cum = money.cumulative(trades)
        big = money.big_trades(trades)
        names = (e["player_2"], e["player_1"]) if swapped else (e["player_1"], e["player_2"])
        e["poly_detail"] = {
            "prices": [[r.ts, (1 - r.price_1) if swapped else r.price_1] for r in px[px["cond"] == cond].itertuples()],
            "cumul": [[r.ts, sign * r.net_1] for r in cum.itertuples()],
            "big": [{"ts": r.ts, "player": names[r.outcome_index], "side": r.side, "usd": r.usd, "price": r.price}
                    for r in big.itertuples()],
        }
    return ok(e)


@app.get("/api/movers/argent")
def movers_money(hours: int = 0):
    """Tendances, argent réel : matchs Polymarket à venir classés par écart d'argent entre les deux joueurs."""
    s = load_settings()
    mk, tr, _ = poly_data(s)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    mk = mk[mk["game_start"] > now]
    if mk.empty:
        return ok([])
    snaps = upcoming_snapshots(s)
    links = {cond: (ev, sw) for ev, (cond, sw) in (poly_links(s, snaps, mk).items() if not snaps.empty else [])}
    since = now - pd.Timedelta(hours=hours) if hours else None
    out = []
    for m in mk.itertuples():
        trades = tr[tr["cond"] == m.cond]
        p = money.pressure(trades, since)
        total = p["usd_1"] + p["usd_2"]
        if total < 50:
            continue
        side = 1 if p["usd_1"] >= p["usd_2"] else 2
        cum = money.cumulative(trades if since is None else trades[trades["ts"] >= since])
        ev, _ = links.get(m.cond, (None, False))
        out.append({"cond": m.cond, "event_id": ev, "start": m.game_start, "tournament": m.tournament,
                    "series": m.series, "player_1": m.player_1, "player_2": m.player_2, "side": side,
                    "player": m.player_1 if side == 1 else m.player_2, "usd_lead": max(p["usd_1"], p["usd_2"]),
                    "usd_total": total, "share": max(p["usd_1"], p["usd_2"]) / total, "n": p["n"], "n_big": p["n_big"],
                    "volume": m.volume, "price": m.price_1 if side == 1 else 1 - m.price_1,
                    "spark": [(v if side == 1 else -v) for v in cum["net_1"]]})
    out.sort(key=lambda r: -(2 * r["usd_lead"] - r["usd_total"]))
    return ok(out)


@app.get("/api/movers")
def movers(hours: int = 0):
    """Tendances : matchs à venir classés par flux estimé (depuis l'ouverture, ou sur les `hours` dernières heures)."""
    s = load_settings()
    snaps = upcoming_snapshots(s)
    if snaps.empty:
        return ok([])
    since = snaps["fetched_at"].max() - pd.Timedelta(hours=hours) if hours else None
    fx = flux.summary(snaps, s.sharp_books, s.anj_books, since=since)
    best = flux.best_odds(snaps, s.anj_books).set_index("event_id")
    info = snaps.sort_values("fetched_at").groupby("event_id").tail(1).set_index("event_id")
    t = flux.timeline(snaps, s.sharp_books, s.anj_books)
    out = []
    for r in fx.itertuples():
        i = info.loc[r.event_id]
        side = r.vers or (1 if r.flux >= 0 else 2)
        b = best.loc[r.event_id] if r.event_id in best.index else None
        spark = t[t["event_id"] == r.event_id]
        out.append({"event_id": r.event_id, "start": i["commence_time"], "tournament": tournament(i["sport_key"]),
                    "player_1": i["player_1"], "player_2": i["player_2"], "side": side,
                    "player": i[f"player_{side}"], "flux": abs(r.flux), "vers": r.vers, "origine": r.origine,
                    "delta_fr": r.delta_fr, "delta_sharp": r.delta_sharp, "n_releves": r.n_releves,
                    "cote_ouv": None if b is None else b[f"ouv_{side}"], "cote": None if b is None else b[f"cote_{side}"],
                    "spark": [(v if side == 1 else 1 - v) for v in spark[r.source].dropna()]})
    out.sort(key=lambda m: -m["flux"])
    return ok(out)


# --------------------------------------------------------------------------- paris
class BetIn(BaseModel):
    match: str
    selection: str
    book: str
    odds: float
    stake: float
    p: float | None = None
    event_id: str | None = None
    tour: str | None = None


class SettleIn(BaseModel):
    result: str


@app.get("/api/bets")
def bets():
    from tennisvalue.tracking.bets import bankroll_report, update_clv

    s = load_settings()
    con = db.connect(s.db_path)
    update_clv(con, s.sharp_books)
    rep = bankroll_report(con, s.bankroll)
    con.close()
    b = rep.pop("bets")
    settled = b[b["status"].isin(["gagne", "perdu", "annule"])]
    rep["curve"] = [s.bankroll] + (s.bankroll + settled["pnl"].cumsum()).tolist()
    b["book_name"] = b["book"].map(book_name)
    rep["bets"] = b.sort_values("placed_at", ascending=False).to_dict("records")
    return ok(rep)


@app.post("/api/bets")
def add(bet: BetIn):
    from tennisvalue.tracking.bets import add_bet

    if bet.odds <= 1 or bet.stake <= 0:
        raise HTTPException(400, "Cote et mise doivent être positives.")
    s = load_settings()
    con = db.connect(s.db_path)
    bid = add_bet(con, match=bet.match, selection=bet.selection, book=bet.book, odds=bet.odds, stake=bet.stake,
                  p_decision=bet.p, event_id=bet.event_id, tour=bet.tour)
    con.close()
    return ok({"bet_id": bid})


@app.post("/api/bets/{bet_id}/settle")
def settle(bet_id: int, body: SettleIn):
    from tennisvalue.tracking.bets import settle_bet

    s = load_settings()
    con = db.connect(s.db_path)
    try:
        settle_bet(con, bet_id, body.result)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    finally:
        con.close()
    return ok({"bet_id": bet_id, "status": body.result})


@app.delete("/api/bets/{bet_id}")
def delete(bet_id: int):
    from tennisvalue.tracking.bets import delete_bet

    s = load_settings()
    con = db.connect(s.db_path)
    delete_bet(con, bet_id)
    con.close()
    return ok({"bet_id": bet_id})


# --------------------------------------------------------------------------- réglages et actions
@app.get("/api/settings")
def get_settings():
    s = load_settings()
    return ok({k: getattr(s, k) for k in EDITABLE})


@app.put("/api/settings")
async def put_settings(request: Request):
    data = await request.json()
    try:
        updates = {k: EDITABLE[k](v) for k, v in data.items() if k in EDITABLE}
    except (TypeError, ValueError):
        raise HTTPException(400, "Valeur invalide.") from None
    if updates.get("decision_source", "sharp") not in ("sharp", "confirmed", "model"):
        raise HTTPException(400, "Source de décision inconnue.")
    if "min_odds" in updates and "max_odds" in updates and updates["min_odds"] >= updates["max_odds"]:
        raise HTTPException(400, "La cote minimale doit être inférieure à la cote maximale.")
    save_settings(updates)
    _cache.pop("pred_key", None)
    return get_settings()


def _busy():
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Une autre opération est en cours, réessayer dans un instant.")


@app.post("/api/update")
def update():
    """Récupère les relevés faits par GitHub (git pull) et les ajoute à la base."""
    _busy()
    try:
        s = load_settings()
        if sync.is_git_repo():
            try:
                sync.git_pull()
            except sync.SyncError as e:
                raise HTTPException(502, f"Impossible de joindre GitHub (connexion internet ?) : {e}") from None
        con = db.connect(s.db_path)
        n = import_snapshots(con, s.snapshots_dir)
        n_poly = pm.import_files(con, s.poly_dir)
        con.close()
        return ok({"imported": n, "imported_poly": n_poly, "results_newer": sync.status(s)["results_newer"],
                   "new_version": sync.code_version() != VERSION})
    finally:
        _lock.release()


@app.post("/api/fetch")
def fetch():
    """Relevé immédiat : clé locale si elle existe, sinon workflow GitHub (1 à 2 minutes)."""
    _busy()
    try:
        s = load_settings()
        try:
            if s.odds_api_key:
                r = sync.fetch_local(s)
                try:                                  # Polymarket : gratuit, mais peut être injoignable
                    con = db.connect(s.db_path)
                    pm.export(pm.collect(pm.PolymarketClient(), pm.latest(s.poly_dir), series=s.poly_series), s.poly_dir)
                    pm.import_files(con, s.poly_dir)
                    con.close()
                except Exception:
                    pass
                return ok({"message": f"{r['n']} cotes relevées. Crédits restants : {r['remaining']}."})
            if sync.gh_ready():
                url = sync.fetch_remote()
                sync.git_pull()
                con = db.connect(s.db_path)
                n = import_snapshots(con, s.snapshots_dir)
                pm.import_files(con, s.poly_dir)
                con.close()
                return ok({"message": f"{n} cotes relevées sur GitHub.", "url": url})
        except Exception as e:  # réseau, clé refusée, GitHub…
            raise HTTPException(502, f"Le relevé a échoué : {e}") from None
        raise HTTPException(400, "Pas de clé The Odds API sur cet ordinateur et GitHub CLI non connecté : "
                                 "lancer scripts/install_chromebook.sh.")
    finally:
        _lock.release()


@app.post("/api/rebuild")
def rebuild():
    from tennisvalue import pipeline

    _busy()
    try:
        s = load_settings()
        pipeline.build_all(s, log=lambda *_: None)
        return ok({"message": "Modèle recalculé."})
    finally:
        _lock.release()


@app.post("/api/results")
async def results(request: Request):
    """Dépôt d'un fichier Excel tennis-data (corps de la requête), rangé dans data/raw."""
    from tennisvalue.ingest.tennisdata import save_results_file

    content = await request.body()
    try:
        r = save_results_file(content, load_settings().raw_dir)
    except Exception as e:  # fichier illisible, autre site, saison plus courte…
        raise HTTPException(400, str(e)) from None
    return ok(r)


@app.get("/api/analysis")
def analysis():
    s = load_settings()
    path = s.reports_dir / "backtest.json"
    if not path.exists():
        return ok(None)
    rep = json.loads(path.read_text())
    return ok({"genere_le": rep["genere_le"], "evaluation": rep["evaluation"]["scores"],
               "n_matchs": rep["evaluation"]["n_matchs"], "periode": rep["evaluation"]["periode"],
               "backtest": rep["backtest"]})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def run(port: int = 8501) -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
