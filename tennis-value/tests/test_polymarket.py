"""Collecte Polymarket, sur des réponses d'API fictives (même structure que les vraies)."""
import json
from datetime import datetime

import pandas as pd

from tennisvalue import db
from tennisvalue.ingest import polymarket as pm

NOW = datetime(2026, 10, 28, 10, 0)


def _event(slug, series, start, players=("Novak Djokovic", "Ugo Humbert"), volume=30000.0):
    ml = {"sportsMarketType": "moneyline", "conditionId": f"0x{slug}", "gameStartTime": start, "closed": False,
          "outcomes": json.dumps(list(players)), "outcomePrices": json.dumps(["0.695", "0.305"]),
          "clobTokenIds": json.dumps([f"tok-{slug}-1", f"tok-{slug}-2"]), "volumeNum": volume,
          "volume24hr": 12000.0, "liquidityNum": 79000.0}
    other = {"sportsMarketType": "tennis_match_totals", "conditionId": f"0x{slug}-tot"}
    return {"slug": slug, "seriesSlug": series, "title": f"Swiss Indoors Basel: {players[0]} vs {players[1]}",
            "markets": [other, ml]}


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **params):
        self.calls.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("/events"):
            return [] if params["offset"] else [
                _event("djo", "atp", "2026-10-28 14:30:00+00"),
                _event("itf", "itf", "2026-10-28 12:00:00+00"),                 # circuit non suivi
                _event("loin", "atp", "2026-11-05 12:00:00+00"),                # hors fenêtre
                _event("fini", "wta", "2026-10-28 05:00:00+00"),                # commencé il y a 5 h
            ]
        if url.endswith("/trades"):
            return [{"timestamp": 1793100000, "outcomeIndex": 0, "side": "BUY", "size": 100, "price": 0.7,
                     "transactionHash": "0xa", "name": "pseudo", "proxyWallet": "0xwallet"},
                    {"timestamp": 1793100600, "outcomeIndex": 1, "side": "BUY", "size": 50, "price": 0.3,
                     "transactionHash": "0xb", "name": "autre", "proxyWallet": "0xwallet2"}]
        if url.endswith("/prices-history"):
            return {"history": [{"t": 1793100000, "p": 0.7}, {"t": 1793103600, "p": 0.695}]}
        raise AssertionError(url)


def test_collect_filters_and_is_incremental(tmp_path):
    c = FakeClient()
    snap = pm.collect(c, None, NOW)
    assert [m["cond"] for m in snap["markets"]] == ["0xdjo"]                       # seulement ATP/WTA, fenêtre
    m = snap["markets"][0]
    assert (m["player_1"], m["price_1"], m["game_start"], m["tournament"]) == \
        ("Novak Djokovic", 0.695, datetime(2026, 10, 28, 14, 30), "Swiss Indoors Basel")
    assert snap["trades"][0] == {"cond": "0xdjo", "ts": 1793100000, "outcome_index": 0, "side": "BUY",
                                 "usd": 70.0, "price": 0.7, "tx": "0xa"}               # ni pseudonyme ni portefeuille
    prices_call = [p for name, p in c.calls if name == "prices-history"][0]
    assert prices_call["interval"] == "max"                                           # nouveau match : tout l'historique
    path = pm.export(snap, tmp_path)
    assert pm.latest(tmp_path)["markets"][0]["cond"] == "0xdjo"

    c2 = FakeClient()
    pm.collect(c2, pm.latest(tmp_path), datetime(2026, 10, 28, 13, 0))
    trades_call = [p for name, p in c2.calls if name == "trades"][0]
    since = int(pd.Timestamp(snap["fetched_at"]).timestamp()) - pm.OVERLAP_S              # UTC
    assert trades_call["start"] == since and [p for n, p in c2.calls if n == "prices-history"][0]["startTs"] == since

    con = db.connect(tmp_path / "t.duckdb")
    assert pm.import_files(con, tmp_path) == 2
    pm.export({**snap, "fetched_at": "2026-10-28T13:00:00"}, tmp_path)               # mêmes transactions
    assert pm.import_files(con, tmp_path) == 0                                        # pas de doublon
    counts = [con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0] for t in ("poly_markets", "poly_trades", "poly_prices")]
    con.close()
    assert counts == [2, 2, 2] and path.name.endswith(".json.gz")


def test_link_names_order_and_time():
    from tennisvalue.market.money import link

    t0 = pd.Timestamp("2026-10-28 14:30")
    events = pd.DataFrame([("e1", "Rémy Bertola", "Denis Shapovalov", t0), ("e2", "Mia Pohánková", "Naomi Osaka", t0),
                           ("e3", "Alex de Minaur", "Learner Tien", t0), ("e4", "Jannik Sinner", "Carlos Alcaraz", t0)],
                          columns=["event_id", "player_1", "player_2", "commence_time"])
    poly = pd.DataFrame([("c1", "Remy Bertola", "Denis Shapovalov", t0 + pd.Timedelta(hours=1)),
                         ("c2", "Naomi Osaka", "Mia Pohankova", t0),                       # ordre inversé
                         ("c3", "A. de Minaur", "L. Tien", t0),                            # noms de famille
                         ("c4", "Jannik Sinner", "Carlos Alcaraz", t0 + pd.Timedelta(days=3))],  # trop loin
                        columns=["cond", "player_1", "player_2", "game_start"])
    assert link(events, poly) == {"e1": ("c1", False), "e2": ("c2", True), "e3": ("c3", False)}


def test_money_pressure_cumulative_and_big_trades():
    from tennisvalue.market.money import big_trades, cumulative, pressure

    t0 = pd.Timestamp("2026-10-28 08:00")
    tr = pd.DataFrame([(t0, 0, "BUY", 1000.0), (t0 + pd.Timedelta(minutes=30), 1, "SELL", 200.0),   # tous deux pour J1
                       (t0 + pd.Timedelta(hours=2), 1, "BUY", 300.0), (t0 + pd.Timedelta(hours=2), 0, "SELL", 100.0)],
                      columns=["ts", "outcome_index", "side", "usd"])
    p = pressure(tr)
    assert (p["usd_1"], p["usd_2"], p["n"], p["n_big"]) == (1200.0, 400.0, 4, 1) and p["share_1"] == 0.75
    assert pressure(tr, since=t0 + pd.Timedelta(hours=1))["share_1"] == 0.0
    assert pd.isna(pressure(tr.iloc[:0])["share_1"])
    c = cumulative(tr)
    assert c["net_1"].tolist() == [1200.0, 800.0] and c["ts"].tolist() == [t0, t0 + pd.Timedelta(hours=2)]
    assert big_trades(tr)["usd"].tolist() == [1000.0]
