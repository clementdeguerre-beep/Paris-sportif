"""Configuration : chemins, seuils de value, règles de mise.

Les valeurs par défaut peuvent être surchargées par un fichier config.yaml à la
racine du projet (voir config.example.yaml).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    db_path: Path = ROOT / "data" / "tennis.duckdb"
    raw_dir: Path = ROOT / "data" / "raw" / "tennis-data"
    models_dir: Path = ROOT / "data" / "models"
    reports_dir: Path = ROOT / "reports"
    snapshots_dir: Path = ROOT / "data" / "snapshots"   # relevés exportés par GitHub Actions

    # Sélection des paris
    decision_source: str = "sharp"   # "sharp" (défaut, cf. backtest), "model" ou "confirmed"
    min_ev: float = 0.03             # value minimale : 3 %
    min_odds: float = 1.30
    max_odds: float = 5.00

    # Mises
    bankroll: float = 1000.0
    kelly_multiplier: float = 0.25
    max_stake_pct: float = 0.02
    max_exposure_pct: float = 0.10

    # Cotes en direct (The Odds API)
    odds_api_key: str = field(default_factory=lambda: os.environ.get("ODDS_API_KEY", ""))
    odds_regions: str = "fr,eu"      # fr = sites ANJ ; eu = Pinnacle, Betfair Exchange…
    # Bookmakers demandés (prioritaires sur odds_regions). Jusqu'à 10 bookmakers = 1 région
    # facturée au lieu de 2 pour « fr,eu » : le relevé coûte deux fois moins de crédits.
    odds_bookmakers: str = "winamax_fr,betclic_fr,unibet_fr,pmu_fr,netbet_fr,pinnacle,betfair_ex_eu"
    sharp_books: tuple = ("pinnacle", "betfair_ex_eu")
    anj_books: tuple = ("betclic_fr", "winamax_fr", "unibet_fr", "pmu_fr", "parionssport_fr", "netbet_fr")

    # Alertes de mouvement
    steam_move_pp: float = 3.0       # mouvement brusque : >= 3 points de proba…
    steam_window_min: int = 60       # … en moins de 60 minutes sur la cote sharp
    stale_gap_pp: float = 2.5        # cote en retard : écart >= 2,5 pts avec la proba sharp


def config_path() -> Path:
    """config.yaml à la racine, ou le fichier désigné par TENNISVALUE_CONFIG."""
    return Path(os.environ.get("TENNISVALUE_CONFIG", ROOT / "config.yaml"))


def save_settings(updates: dict, path: Path | None = None) -> None:
    """Met à jour config.yaml avec `updates` (les autres paramètres, dont la clé API, sont conservés)."""
    path = path or config_path()
    data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    names = {f.name for f in fields(Settings)}
    unknown = set(updates) - names
    if unknown:
        raise ValueError(f"Paramètres inconnus : {', '.join(sorted(unknown))}")
    data.update(updates)
    path.write_text("# Modifié depuis l'onglet Réglages de l'application (voir config.example.yaml).\n"
                    + yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def load_settings(path: Path | None = None) -> Settings:
    s = Settings()
    path = path or config_path()
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k not in names:
                raise ValueError(f"Paramètre inconnu dans {path.name} : {k}")
            cur = getattr(s, k)
            if isinstance(cur, Path):
                v = ROOT / v if not Path(v).is_absolute() else Path(v)
            elif isinstance(cur, tuple):
                v = tuple(v)
            setattr(s, k, v)
    return s
