"""Relevés de cotes partagés par git (GitHub Actions -> ordinateur).

Le workflow .github/workflows/fetch-odds.yml lance `fetch-odds --export` toutes
les 3 heures : chaque relevé devient un nouveau fichier
data/snapshots/AAAA-MM/AAAAMMJJTHHMMSSZ.csv.gz, ajouté au dépôt. Sur l'ordinateur,
`git pull` puis `tennisvalue import-odds` les ajoute à odds_snapshots ; les
lignes déjà présentes (même relevé, match et bookmaker) sont ignorées.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

# Même ordre et mêmes types que la table odds_snapshots (db.SCHEMA).
COLUMNS = {
    "fetched_at": "TIMESTAMP", "event_id": "VARCHAR", "sport_key": "VARCHAR", "commence_time": "TIMESTAMP",
    "player_1": "VARCHAR", "player_2": "VARCHAR", "book": "VARCHAR", "book_update": "TIMESTAMP",
    "odds_1": "DOUBLE", "odds_2": "DOUBLE",
}


def export_snapshot(df: pd.DataFrame, folder: Path, now: datetime | None = None) -> Path:
    """Écrit un relevé dans un nouveau fichier CSV compressé (jamais réécrit ensuite)."""
    now = now or datetime.now(timezone.utc)
    path = folder / now.strftime("%Y-%m") / now.strftime("%Y%m%dT%H%M%SZ.csv.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    df[list(COLUMNS)].to_csv(path, index=False)
    return path


def import_snapshots(con: duckdb.DuckDBPyConnection, folder: Path) -> int:
    """Ajoute à odds_snapshots les relevés exportés absents de la base ; renvoie le nombre de lignes ajoutées."""
    files = sorted(str(p) for p in folder.rglob("*.csv.gz"))
    if not files:
        return 0
    columns = "{" + ", ".join(f"'{k}': '{v}'" for k, v in COLUMNS.items()) + "}"
    before = con.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0]
    con.execute(f"""
        INSERT INTO odds_snapshots
        SELECT t.* FROM read_csv(?, header = true, columns = {columns}) t
        WHERE NOT EXISTS (SELECT 1 FROM odds_snapshots o
                          WHERE o.fetched_at = t.fetched_at AND o.event_id = t.event_id AND o.book = t.book)
    """, [files])
    return con.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] - before
