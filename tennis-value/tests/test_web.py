"""API de l'application (fastapi.testclient), sur une base fictive sans modèle."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from tennisvalue import db
from tennisvalue.web import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = {"db_path": str(tmp_path / "t.duckdb"), "models_dir": str(tmp_path / "models"),
           "reports_dir": str(tmp_path / "reports"), "raw_dir": str(tmp_path / "raw"),
           "snapshots_dir": str(tmp_path / "snapshots")}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("TENNISVALUE_CONFIG", str(tmp_path / "config.yaml"))
    api._cache.clear()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    rows = []
    # Match e1 : l'argent arrive sur Alcaraz chez les français seulement ; e2 : stable.
    for k, lag in enumerate((6, 3, 0)):
        for book, o1, o2 in [("winamax_fr", 2.0 - 0.1 * k, 1.8 + 0.1 * k), ("betclic_fr", 1.95 - 0.1 * k, 1.85 + 0.1 * k),
                             ("pinnacle", 2.05, 1.85)]:
            rows.append(("e1", "tennis_atp_paris_masters", "Carlos Alcaraz", "Jannik Sinner", book, lag, o1, o2))
        rows.append(("e2", "tennis_wta_wuhan_open", "Iga Swiatek", "Coco Gauff", "unibet_fr", lag, 1.6, 2.3))
    con = db.connect(tmp_path / "t.duckdb")
    db.append_table(con, "odds_snapshots", pd.DataFrame([dict(
        fetched_at=now - timedelta(hours=lag), event_id=ev, sport_key=sk, commence_time=now + timedelta(hours=5),
        player_1=p1, player_2=p2, book=book, book_update=now, odds_1=o1, odds_2=o2)
        for ev, sk, p1, p2, book, lag, o1, o2 in rows]))
    con.close()
    return TestClient(api.app)


def test_status_and_index(client):
    st = client.get("/api/status").json()
    assert st["upcoming"] == 2 and not st["model_ready"] and st["last_fetch"].endswith("Z")
    assert st["settings"]["bankroll"] == 1000 and st["open_total"] == 0
    assert "Tennis Value" in client.get("/").text


def test_matches_with_flux_and_best_odds(client):
    ms = {m["event_id"]: m for m in client.get("/api/matches").json()}
    e1 = ms["e1"]
    assert (e1["tournament"], e1["tour"]) == ("ATP Paris Masters", "ATP")
    assert [b["name"] for b in e1["books"]] == ["Betclic", "Winamax", "Pinnacle"]      # français d'abord
    assert (e1["cote_1"], e1["book_1_name"], e1["ouv_1"]) == (1.8, "Winamax", 2.0)
    assert e1["flux"]["vers"] == 1 and e1["flux"]["origine"] == "grand public" and e1["flux"]["flux"] > 3
    assert ms["e2"]["flux"]["origine"] == "stable"


def test_match_detail_series(client):
    d = client.get("/api/matches/e1").json()
    assert {s["name"]: len(s["points"]) for s in d["series"]} == {"Betclic": 3, "Winamax": 3, "Pinnacle": 3}
    assert len(d["consensus"]) == 3 and d["consensus"][0][1] < d["consensus"][-1][1]   # proba d'Alcaraz monte
    assert client.get("/api/matches/inconnu").status_code == 404


def test_movers_sorted_by_flux(client):
    mv = client.get("/api/movers").json()
    assert [m["event_id"] for m in mv] == ["e1", "e2"]
    assert mv[0]["player"] == "Carlos Alcaraz" and (mv[0]["cote_ouv"], mv[0]["cote"]) == (2.0, 1.8)
    assert len(mv[0]["spark"]) == 3
    recent = {m["event_id"]: m for m in client.get("/api/movers?hours=2").json()}
    assert recent["e1"]["n_releves"] == 2                                              # depuis le relevé d'il y a 3 h


def test_bets_lifecycle(client):
    bid = client.post("/api/bets", json={"match": "Carlos Alcaraz - Jannik Sinner", "selection": "Carlos Alcaraz",
                                         "book": "winamax_fr", "odds": 1.8, "stake": 10, "p": 0.6,
                                         "event_id": "e1", "tour": "ATP"}).json()["bet_id"]
    assert client.get("/api/status").json()["open_total"] == 10
    assert client.get("/api/matches").json()[0]["my_bets"][0]["bet_id"] == bid
    assert client.post(f"/api/bets/{bid}/settle", json={"result": "gagne"}).status_code == 200
    rep = client.get("/api/bets").json()
    assert rep["bankroll"] == pytest.approx(1008) and rep["curve"] == [1000, pytest.approx(1008)]
    assert rep["bets"][0]["book_name"] == "Winamax" and rep["bets"][0]["placed_at"].endswith("Z")
    assert client.post(f"/api/bets/{bid}/settle", json={"result": "nul"}).status_code == 400
    client.delete(f"/api/bets/{bid}")
    assert client.get("/api/bets").json()["bets"] == []
    assert client.post("/api/bets", json={"match": "x", "selection": "x", "book": "x", "odds": 1.0,
                                          "stake": 5}).status_code == 400


def test_settings_and_uploads(client):
    assert client.put("/api/settings", json={"bankroll": 500, "min_ev": 0.04}).json()["bankroll"] == 500
    assert client.put("/api/settings", json={"decision_source": "pifometre"}).status_code == 400
    assert client.put("/api/settings", json={"min_odds": 3, "max_odds": 2}).status_code == 400
    assert client.post("/api/results", content=b"pas un fichier excel").status_code == 400
    assert client.get("/api/analysis").json() is None


def test_polymarket_money_in_api(client, tmp_path):
    """Marché Polymarket d'e1 avec les joueurs dans l'ordre inverse : tout doit être réorienté."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    con = db.connect(tmp_path / "t.duckdb")
    con.execute("INSERT INTO poly_markets VALUES (?, 'c1', 'atp-x', 'atp', 'Paris', ?, 'Jannik Sinner', 'Carlos Alcaraz', "
                "0.4, 5000, 1200, 9000)", [now, now + timedelta(hours=5)])
    for k, (oi, side, usd) in enumerate([(1, "BUY", 900.0), (0, "SELL", 100.0), (0, "BUY", 250.0)]):   # 1 000 $ sur Alcaraz
        con.execute("INSERT INTO poly_trades VALUES ('c1', ?, ?, ?, ?, 0.5, ?)", [now - timedelta(hours=k), oi, side, usd, f"0x{k}"])
    con.execute("INSERT INTO poly_prices VALUES ('c1', ?, 0.42), ('c1', ?, 0.40)", [now - timedelta(hours=2), now])
    con.close()
    e1 = {m["event_id"]: m for m in client.get("/api/matches").json()}["e1"]
    p = e1["poly"]                                    # e1 : Alcaraz (J1) - Sinner (J2)
    assert p["price_1"] == pytest.approx(0.6) and (p["usd_1"], p["usd_2"]) == (1000.0, 250.0)
    assert p["share_1"] == pytest.approx(0.8) and p["n_big"] == 1
    d = client.get("/api/matches/e1").json()["poly_detail"]
    assert [round(x[1], 2) for x in d["prices"]] == [0.58, 0.6]
    assert d["cumul"][-1][1] == pytest.approx(750.0) and d["big"][0]["player"] == "Carlos Alcaraz"
    mv = client.get("/api/movers/argent").json()
    assert (mv[0]["event_id"], mv[0]["player"], round(mv[0]["share"], 2)) == ("e1", "Carlos Alcaraz", 0.8)
    assert client.get("/api/status").json()["poly_last"].endswith("Z")
