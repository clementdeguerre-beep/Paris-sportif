import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import yaml

from tennisvalue import db, sync
from tennisvalue.config import ROOT, Settings, load_settings, save_settings
from tennisvalue.ingest.tennisdata import save_results_file
from tennisvalue.market.devig import fair_two_way
from tennisvalue.market.staking import StakingRules, scale_to_exposure
from tennisvalue.tracking.bets import add_bet, delete_bet, update_clv


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _snap(event_id, fetched_at, start, o1, o2, book="pinnacle"):
    return dict(fetched_at=fetched_at, event_id=event_id, sport_key="tennis_atp_paris_masters", commence_time=start,
                player_1="A", player_2="B", book=book, book_update=fetched_at, odds_1=o1, odds_2=o2)


def test_db_compares_in_utc(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    con = db.connect(tmp_path / "t.duckdb")
    assert con.execute("SELECT current_setting('TimeZone')").fetchone()[0] == "UTC"
    now_db = con.execute("SELECT now()::TIMESTAMP").fetchone()[0]
    con.close()
    assert abs((now_db - _utcnow()).total_seconds()) < 60


def test_save_settings_keeps_other_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('odds_api_key: "secret"\nbankroll: 1000\n')
    save_settings({"bankroll": 500.0, "min_ev": 0.04}, path)
    data = yaml.safe_load(path.read_text())
    assert data == {"odds_api_key": "secret", "bankroll": 500.0, "min_ev": 0.04}
    s = load_settings(path)
    assert (s.bankroll, s.min_ev, s.odds_api_key) == (500.0, 0.04, "secret")
    with pytest.raises(ValueError):
        save_settings({"bankrol": 1}, path)


def test_exposure_counts_open_bets():
    rules = StakingRules(max_exposure_pct=0.10)
    assert scale_to_exposure([30, 30], 1000, rules) == [30, 30]
    assert scale_to_exposure([30, 30], 1000, rules, open_total=70) == [15, 15]   # reste 30 €
    assert scale_to_exposure([30, 30], 1000, rules, open_total=150) == [0, 0]


def test_clv_waits_for_match_start(tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    now = _utcnow()
    db.append_table(con, "odds_snapshots", pd.DataFrame([
        _snap("futur", now - timedelta(hours=1), now + timedelta(hours=2), 2.0, 1.9),
        _snap("passe", now - timedelta(hours=5), now - timedelta(hours=1), 2.2, 1.7),
        _snap("passe", now - timedelta(hours=2), now - timedelta(hours=1), 2.0, 1.85),   # clôture
    ]))
    b1 = add_bet(con, match="A - B", selection="A", book="winamax_fr", odds=2.1, stake=10, event_id="futur")
    b2 = add_bet(con, match="A - B", selection="A", book="winamax_fr", odds=2.1, stake=10, event_id="passe")
    assert update_clv(con, ("pinnacle",)) == 1
    clv = dict(con.execute("SELECT bet_id, clv FROM bets").fetchall())
    con.close()
    assert clv[b1] is None
    assert clv[b2] == pytest.approx(2.1 * fair_two_way(2.0, 1.85)[0] - 1)   # cote du dernier relevé


def test_delete_bet(tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    bid = add_bet(con, match="A - B", selection="A", book="winamax_fr", odds=2.0, stake=5)
    delete_bet(con, bid)
    assert con.execute("SELECT count(*) FROM bets").fetchone()[0] == 0
    con.close()


def test_status(tmp_path):
    s = Settings(db_path=tmp_path / "t.duckdb", models_dir=tmp_path / "models", raw_dir=tmp_path / "raw")
    st = sync.status(s)
    assert st["last_fetch"] is None and st["upcoming"] == 0 and not st["model_ready"] and not st["results_newer"]
    now = _utcnow()
    con = db.connect(s.db_path)
    db.append_table(con, "odds_snapshots", pd.DataFrame([
        _snap("e1", now, now + timedelta(hours=3), 2.0, 1.9), _snap("e2", now, now - timedelta(hours=1), 2.0, 1.9)]))
    con.close()
    st = sync.status(s)
    assert st["last_fetch"] == now and st["upcoming"] == 1



def test_save_results_file(tmp_path):
    src = ROOT / "data/raw/tennis-data/wta"
    content = (src / "2020.xlsx").read_bytes()
    r = save_results_file(content, tmp_path)
    assert (r["tour"], r["season"], r["before"]) == ("WTA", 2020, 0) and r["n"] > 500
    assert (tmp_path / "wta/2020.xlsx").read_bytes() == content
    assert save_results_file(content, tmp_path)["before"] == r["n"]          # même fichier : accepté
    # Plus court que le fichier en place (ici la saison 2019, plus longue) : refusé.
    (tmp_path / "wta/2020.xlsx").write_bytes((src / "2019.xlsx").read_bytes())
    with pytest.raises(ValueError, match="rien n'est remplacé"):
        save_results_file(content, tmp_path)
    assert [f.name for f in (tmp_path / "wta").iterdir()] == ["2020.xlsx"]  # pas de fichier .part
    buf = io.BytesIO()
    pd.DataFrame({"a": [1]}).to_excel(buf, index=False)
    with pytest.raises(ValueError, match="pas un fichier de résultats"):
        save_results_file(buf.getvalue(), tmp_path)
