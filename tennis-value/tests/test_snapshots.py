from datetime import datetime, timezone

import pandas as pd

from tennisvalue import db
from tennisvalue.ingest.snapshots import export_snapshot, import_snapshots


def _snapshot(fetched_at):
    return pd.DataFrame([
        dict(fetched_at=fetched_at, event_id="e1", sport_key="tennis_atp_paris", commence_time=pd.Timestamp("2026-10-28 18:00"),
             player_1="Carlos Alcaraz", player_2="Jannik Sinner", book=book, book_update=pd.NaT if book == "winamax_fr" else fetched_at,
             odds_1=o1, odds_2=o2)
        for book, o1, o2 in [("pinnacle", 2.10, 1.80), ("winamax_fr", 2.05, 1.75)]
    ])


def test_export_then_import_is_idempotent(tmp_path):
    folder = tmp_path / "snapshots"
    t1 = pd.Timestamp("2026-10-27 07:07:03.123456")
    p1 = export_snapshot(_snapshot(t1), folder, now=datetime(2026, 10, 27, 7, 7, 3, tzinfo=timezone.utc))
    assert p1.relative_to(folder).as_posix() == "2026-10/20261027T070703Z.csv.gz"

    con = db.connect(tmp_path / "t.duckdb")
    db.append_table(con, "odds_snapshots", _snapshot(t1))         # relevé déjà présent en base
    assert import_snapshots(con, folder) == 0

    export_snapshot(_snapshot(t1 + pd.Timedelta(hours=3)), folder, now=datetime(2026, 10, 27, 10, 7, tzinfo=timezone.utc))
    assert import_snapshots(con, folder) == 2
    assert import_snapshots(con, folder) == 0

    rows = db.read(con, "SELECT * FROM odds_snapshots ORDER BY fetched_at, book")
    con.close()
    assert len(rows) == 4
    assert rows.fetched_at.iloc[0] == t1                               # microsecondes conservées
    assert rows.book_update.isna().sum() == 2
    assert rows.odds_1.tolist() == [2.10, 2.05, 2.10, 2.05]


def test_import_without_folder(tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    assert import_snapshots(con, tmp_path / "absent") == 0
    con.close()
