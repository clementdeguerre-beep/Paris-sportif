"""Ingestion des fichiers historiques de tennis-data.co.uk (résultats + cotes).

Source : http://www.tennis-data.co.uk (rubrique « Historical data »), un fichier
xlsx par saison et par circuit. Colonnes de cotes utilisées :
- B365W/B365L : Bet365 ;
- PSW/PSL     : Pinnacle (quasi absent à partir de 2026 : Pinnacle a fermé son
                API publique le 23/07/2025) ;
- BFEW/BFEL   : Betfair Exchange (à partir de fin 2025) ;
- MaxW/MaxL   : meilleure cote relevée parmi les bookmakers suivis ;
- AvgW/AvgL   : cote moyenne des bookmakers suivis.
Le moment exact du relevé des cotes est décrit dans le fichier notes.txt du
site : on le traite ici comme une cote d'avant-match unique (pas d'historique
d'évolution). La CLV réelle se mesure avec le module de cotes en direct.

Les fichiers sont placés dans data/raw/tennis-data/{atp,wta}/AAAA.xlsx.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

BOOKS = {"B365": "b365", "PS": "ps", "BFE": "bfe", "Max": "max", "Avg": "avg"}


def norm_name(name: str) -> str:
    """Clé de joueur stable : minuscules, sans accents, tirets/points/espaces unifiés.

    « Auger-Aliassime F. » et « Auger Aliassime F » donnent la même clé.
    """
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", " ").replace(".", " ").replace("'", " ")
    return re.sub(r"\s+", " ", s).strip()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def read_file(path: Path, tour: str) -> pd.DataFrame:
    raw = pd.read_excel(path)
    raw = raw[raw["Winner"].notna() & raw["Loser"].notna()].reset_index(drop=True)
    tier_col = "Series" if "Series" in raw.columns else "Tier"
    df = pd.DataFrame(
        {
            "tour": tour.upper(),
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "tournament": raw["Tournament"].astype(str).str.strip(),
            "location": raw["Location"].astype(str).str.strip(),
            "tier": raw[tier_col].astype(str).str.strip(),
            "court": raw["Court"].astype(str).str.strip(),
            "surface": raw["Surface"].astype(str).str.strip(),
            "round": raw["Round"].astype(str).str.strip(),
            "best_of": _num(raw["Best of"]).fillna(3).astype(int),
            "winner": raw["Winner"].astype(str).str.strip(),
            "loser": raw["Loser"].astype(str).str.strip(),
            "w_rank": _num(raw["WRank"]),
            "l_rank": _num(raw["LRank"]),
            "w_pts": _num(raw["WPts"]),
            "l_pts": _num(raw["LPts"]),
            "comment": raw["Comment"].astype(str).str.strip(),
            "w_sets": _num(raw["Wsets"]),
            "l_sets": _num(raw["Lsets"]),
        }
    )
    w_games = np.zeros(len(raw))
    l_games = np.zeros(len(raw))
    n_sets = np.zeros(len(raw))
    tb_sets = np.zeros(len(raw))
    for i in range(1, 6):
        if f"W{i}" not in raw:
            continue
        w = _num(raw[f"W{i}"])
        l = _num(raw[f"L{i}"])
        played = w.notna() & l.notna()
        df[f"w{i}"] = w
        df[f"l{i}"] = l
        w_games += w.fillna(0).to_numpy()
        l_games += l.fillna(0).to_numpy()
        n_sets += played.to_numpy()
        tb_sets += (played & (((w == 7) & (l == 6)) | ((w == 6) & (l == 7)))).to_numpy()
    df["w_games"] = w_games
    df["l_games"] = l_games
    df["n_sets"] = n_sets
    df["tb_sets"] = tb_sets
    for col, key in BOOKS.items():
        for side, s in (("W", "w"), ("L", "l")):
            name = f"{col}{side}"
            v = _num(raw[name]) if name in raw.columns else pd.Series(np.nan, index=raw.index)
            df[f"odds_{key}_{s}"] = v.where(v > 1.0)
    df["file_row"] = np.arange(len(df))
    return df


def save_results_file(content: bytes, raw_dir: Path) -> dict:
    """Range un fichier téléchargé sur tennis-data dans raw_dir/{atp,wta}/AAAA.xlsx.

    Circuit reconnu à la 1re colonne (« ATP » ou « WTA »), saison = année la plus
    fréquente des dates. Refuse un fichier illisible ou plus court que celui en place.
    """
    raw = pd.read_excel(io.BytesIO(content), nrows=5000)
    tour = next((t for t in ("ATP", "WTA") if t in raw.columns), None)
    if tour is None or "Date" not in raw.columns:
        raise ValueError("ce n'est pas un fichier de résultats tennis-data (colonne ATP ou WTA absente)")
    season = int(pd.to_datetime(raw["Date"], errors="coerce").dt.year.mode()[0])
    path = raw_dir / tour.lower() / f"{season}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{season}.xlsx.part")
    tmp.write_bytes(content)
    try:
        n = len(read_file(tmp, tour))
        before = len(read_file(path, tour)) if path.exists() else 0
        if n < before:
            raise ValueError(f"{n} matchs contre {before} dans le fichier déjà présent : rien n'est remplacé")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"tour": tour, "season": season, "n": n, "before": before}


def match_id(row) -> str:
    key = f"{row.tour}|{row.date}|{row.tournament}|{row.winner}|{row.loser}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def load_all(raw_dir: Path) -> pd.DataFrame:
    """Charge tous les fichiers ATP/WTA et renvoie les matchs triés chronologiquement."""
    frames = []
    for tour in ("atp", "wta"):
        for path in sorted((raw_dir / tour).glob("*.xlsx")):
            df = read_file(path, tour)
            df["season"] = int(path.stem[:4])
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"Aucun fichier xlsx trouvé dans {raw_dir}/atp ou {raw_dir}/wta")
    df = pd.concat(frames, ignore_index=True)
    df["winner_key"] = df["winner"].map(norm_name)
    df["loser_key"] = df["loser"].map(norm_name)
    df["match_id"] = [match_id(r) for r in df.itertuples()]
    df = df.drop_duplicates("match_id")
    df["completed"] = df["comment"].str.lower().eq("completed")
    df["walkover"] = df["comment"].str.lower().str.contains("walkover|w/o", regex=True)
    # Ordre chronologique : date du match puis ordre du fichier (tours successifs d'un tournoi).
    df = df.sort_values(["tour", "date", "season", "file_row"]).reset_index(drop=True)
    df["seq"] = np.arange(len(df))
    return df
