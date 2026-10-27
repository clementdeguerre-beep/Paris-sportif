"""Base de données locale (DuckDB : un seul fichier, rapide pour l'analytique).

Tables principales :
- matches         : résultats + cotes historiques (tennis-data) ;
- features        : variables d'avant-match + probabilités (modèles, marché) ;
- bt_summary      : synthèse du backtest par stratégie ;
- odds_snapshots  : cotes en direct horodatées (ouverture -> clôture) ;
- predictions     : sorties du scanner (matchs à venir) ;
- bets            : mes paris réels (suivi de bankroll et CLV).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshots (
    fetched_at TIMESTAMP, event_id VARCHAR, sport_key VARCHAR, commence_time TIMESTAMP,
    player_1 VARCHAR, player_2 VARCHAR, book VARCHAR, book_update TIMESTAMP,
    odds_1 DOUBLE, odds_2 DOUBLE
);
CREATE SEQUENCE IF NOT EXISTS bet_id_seq START 1;
CREATE TABLE IF NOT EXISTS bets (
    bet_id INTEGER DEFAULT nextval('bet_id_seq') PRIMARY KEY,
    placed_at TIMESTAMP, event_id VARCHAR, tour VARCHAR, match VARCHAR, selection VARCHAR,
    book VARCHAR, odds DOUBLE, stake DOUBLE, p_decision DOUBLE, ev DOUBLE,
    status VARCHAR DEFAULT 'ouvert',            -- ouvert / gagne / perdu / annule
    closing_odds_fair DOUBLE,                   -- cote juste de clôture (sharp, sans marge)
    clv DOUBLE, pnl DOUBLE, note VARCHAR
);
"""


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def write_table(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame) -> None:
    con.register("_tmp_df", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _tmp_df")
    con.unregister("_tmp_df")


def append_table(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame) -> None:
    con.register("_tmp_df", df)
    con.execute(f"INSERT INTO {name} SELECT * FROM _tmp_df")
    con.unregister("_tmp_df")


def read(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).df()
