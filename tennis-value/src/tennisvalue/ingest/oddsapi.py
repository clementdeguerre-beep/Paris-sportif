"""Cotes en direct via The Odds API (https://the-odds-api.com).

- Régions : « fr » = sites agréés ANJ (Betclic, Winamax, Unibet, PMU, NetBet…) ;
  « eu » = Pinnacle (relevé sur son site public, avec un léger délai selon la
  documentation), Betfair Exchange (betfair_ex_eu), etc.
- Couverture tennis : tournois ATP/WTA de Grand Chelem, 1000 et 500 (pas de
  Challenger). Les clés de tournoi actives se lisent sur /v4/sports.
- Coût d'un appel « odds » = nombre de marchés x nombre de régions (en crédits).
  Exemple : h2h sur fr + eu = 2 crédits par tournoi et par relevé.

Chaque relevé est stocké horodaté dans odds_snapshots : la première ligne d'un
match donne la cote d'ouverture observée, la dernière avant le début du match
donne la cote de clôture. Plus les relevés sont fréquents, plus la mesure des
mouvements et de la CLV est précise (et plus on consomme de crédits).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pandas as pd

from tennisvalue.ingest.tennisdata import norm_name

BASE = "https://api.the-odds-api.com/v4"

# Surface et format par clé de tournoi (The Odds API). Clé inconnue -> dur, 3 sets (signalé).
SURFACE_BY_KEYWORD = {
    "french_open": "Clay", "barcelona": "Clay", "hamburg": "Clay", "italian": "Clay",
    "madrid": "Clay", "monte_carlo": "Clay", "munich": "Clay", "charleston": "Clay",
    "strasbourg": "Clay", "stuttgart": "Clay",
    "wimbledon": "Grass", "halle": "Grass", "queens": "Grass", "bad_homburg": "Grass",
    "german_open": "Grass",
}
HARD_KEYWORDS = (
    "aus_open", "canadian", "china", "cincinnati", "dubai", "indian_wells", "japan", "miami",
    "paris_masters", "qatar", "shanghai", "us_open", "washington", "guadalajara", "monterrey",
    "singapore", "wuhan",
)
SLAM_KEYWORDS = ("aus_open", "french_open", "wimbledon", "us_open")


def tournament_context(sport_key: str) -> dict:
    surface = next((s for k, s in SURFACE_BY_KEYWORD.items() if k in sport_key), "Hard")
    slam = any(k in sport_key for k in SLAM_KEYWORDS)
    tour = "ATP" if "_atp_" in sport_key else "WTA"
    return {
        "tour": tour,
        "surface": surface,
        "tier": "Grand Slam" if slam else "Other",
        "best_of": 5 if (slam and tour == "ATP") else 3,
        "surface_known": any(k in sport_key for k in (*SURFACE_BY_KEYWORD, *HARD_KEYWORDS)),
    }


class OddsApiClient:
    def __init__(self, api_key: str, timeout: float = 20.0):
        if not api_key:
            raise ValueError("Clé API manquante : définir ODDS_API_KEY (variable d'environnement) ou config.yaml")
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout)
        self.remaining: str | None = None

    def _get(self, path: str, **params):
        r = self.client.get(f"{BASE}{path}", params={"apiKey": self.api_key, **params})
        r.raise_for_status()
        self.remaining = r.headers.get("x-requests-remaining", self.remaining)
        return r.json()

    def tennis_sports(self) -> list[dict]:
        """Tournois de tennis actuellement ouverts aux paris."""
        return [s for s in self._get("/sports") if s["key"].startswith("tennis_") and s.get("active")]

    def odds(self, sport_key: str, regions: str = "fr,eu", markets: str = "h2h") -> list[dict]:
        return self._get(f"/sports/{sport_key}/odds", regions=regions, markets=markets, oddsFormat="decimal")


def parse_events(events: list[dict], fetched_at: datetime | None = None) -> pd.DataFrame:
    """Aplatit la réponse JSON en une ligne par (match, bookmaker)."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows = []
    for ev in events:
        p1, p2 = ev["home_team"], ev["away_team"]
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                prices = {o["name"]: o["price"] for o in mk.get("outcomes", [])}
                if p1 not in prices or p2 not in prices:
                    continue
                rows.append({
                    "fetched_at": fetched_at.replace(tzinfo=None),
                    "event_id": ev["id"], "sport_key": ev["sport_key"],
                    "commence_time": pd.Timestamp(ev["commence_time"]).tz_convert(None),
                    "player_1": p1, "player_2": p2, "book": bk["key"],
                    "book_update": pd.Timestamp(mk.get("last_update") or bk.get("last_update")).tz_convert(None),
                    "odds_1": float(prices[p1]), "odds_2": float(prices[p2]),
                })
    return pd.DataFrame(rows, columns=[
        "fetched_at", "event_id", "sport_key", "commence_time", "player_1", "player_2",
        "book", "book_update", "odds_1", "odds_2",
    ])


def name_candidates(full_name: str) -> list[str]:
    """Clés possibles au format tennis-data (« Nom I. ») pour un nom complet.

    « Carlos Alcaraz » -> « alcaraz c » ; « Felix Auger-Aliassime » ->
    « auger aliassime f » ; « Juan Manuel Cerundolo » -> « cerundolo j m », etc.
    """
    parts = norm_name(full_name).split()
    if len(parts) < 2:
        return [norm_name(full_name)]
    cands = []
    for split in range(1, len(parts)):
        first, last = parts[:split], parts[split:]
        initials = " ".join(p[0] for p in first)
        cands.append(f"{' '.join(last)} {initials}")
        cands.append(f"{' '.join(last)} {first[0][0]}")
    return list(dict.fromkeys(cands))


def match_player(full_name: str, known_keys: set[str]) -> str | None:
    """Retrouve la clé tennis-data d'un joueur ; None si inconnu ou ambigu."""
    hits = [c for c in name_candidates(full_name) if c in known_keys]
    return hits[0] if hits else None
