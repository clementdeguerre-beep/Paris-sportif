"""Actualités des joueurs : titres de la semaine (Google Actualités, flux RSS public) et mots-clés.

Pour chaque joueur, deux recherches (presse française et anglophone) sur les 7 derniers
jours. Chaque titre est classé par mots-clés : blessure / forfait, fatigue, moral en
baisse, confiance. C'est un repérage, pas une lecture : un titre « X bat Y, qui
abandonne » marque les deux joueurs, d'où l'affichage des titres eux-mêmes.

Ces informations n'entrent pas dans le calcul des chances : il n'existe pas d'historique
d'actualités pour mesurer leur effet sur les résultats passés, et un poids choisi au jugé
fabriquerait de fausses « bonnes cotes ». Elles servent d'alerte avant de parier.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

RSS = "https://news.google.com/rss/search"
LOCALES = ({"hl": "fr", "gl": "FR", "ceid": "FR:fr"}, {"hl": "en-US", "gl": "US", "ceid": "US:en"})
TTL = timedelta(hours=3)

# Racines de mots (titres sans accents, en minuscules), français et anglais.
KEYWORDS = {
    "blessure": [r"bless", r"douleur", r"\bmal (au|a la|aux)\b", r"poignet", r"genou", r"cheville", r"epaule",
                 r"cuisse", r"mollet", r"abdo", r"hanche", r"\bcoude", r"ischio", r"abandon", r"forfait",
                 r"se retire", r"operation", r"opere", r"malade", r"virus", r"fievre", r"ampoule",
                 r"injur", r"\bpain\b", r"wrist", r"\bknee", r"ankle", r"shoulder", r"thigh", r"\bcalf\b",
                 r"\bhip\b", r"elbow", r"hamstring", r"abdominal", r"retire", r"withdr[ae]w", r"pull(s|ed)? out",
                 r"surgery", r"strain", r"sprain", r"\bill(ness)?\b", r"\bsick", r"fever", r"blister"],
    "fatigue": [r"fatigu", r"epuis", r"exhaust", r"\btired", r"jet.?lag", r"decalage horaire", r"marathon"],
    "moral": [r"frustr", r"colere", r"\bdoute", r"\bcrise\b", r"perd\w* confiance", r"demotiv", r"struggl",
              r"\bslump", r"crisis", r"angry", r"lack of confidence", r"burn.?out", r"meltdown"],
    "confiance": [r"confian", r"en forme", r"sensations", r"serein", r"invaincu", r"serie de victoires",
                  r"\bsacre", r"remporte le titre", r"confident", r"in form", r"great form", r"winning streak",
                  r"unbeaten", r"wins? (the |his |her )?(first |maiden )?title", r"best tennis"],
}
_PATTERNS = {k: re.compile("|".join(v)) for k, v in KEYWORDS.items()}
LABELS = {"blessure": "Blessure / forfait", "fatigue": "Fatigue", "moral": "Moral en baisse", "confiance": "Confiance"}


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def tags(title: str) -> list[str]:
    t = _plain(title)
    return [k for k, p in _PATTERNS.items() if p.search(t)]


def parse_rss(xml_text: str) -> list[dict]:
    """Titres d'un flux RSS : titre (sans le nom du journal), lien, journal, date (UTC)."""
    out = []
    for item in ET.fromstring(xml_text).iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            published = None
        out.append({"title": title, "link": (item.findtext("link") or "").strip(), "source": source,
                    "published": published, "tags": tags(title)})
    return out


def fetch_player(name: str, client: httpx.Client | None = None) -> list[dict]:
    """Titres des 7 derniers jours sur un joueur, presse française puis anglophone, sans doublons."""
    client = client or httpx.Client(timeout=15, follow_redirects=True)
    seen, out = set(), []
    for loc in LOCALES:
        r = client.get(RSS, params={"q": f'"{name}" tennis when:7d', **loc})
        r.raise_for_status()
        for it in parse_rss(r.text):
            key = _plain(it["title"])[:80]
            if key not in seen:
                seen.add(key)
                out.append(it)
    out.sort(key=lambda it: it["published"] or datetime.min, reverse=True)
    return out


def summary(items: list[dict], now: datetime | None = None, days: int = 7) -> dict:
    """Nombre de titres par mot-clé sur les `days` derniers jours, et le titre le plus récent de chaque."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    recent = [it for it in items if it["published"] is None or now - it["published"] <= timedelta(days=days)]
    out = {"n": len(recent)}
    for k in KEYWORDS:
        hits = [it for it in recent if k in it["tags"]]
        out[k] = len(hits)
        out[f"{k}_titre"] = hits[0]["title"] if hits else None
    return out


class NewsCache:
    """Actualités en mémoire, rafraîchies au plus toutes les 3 heures par joueur."""

    def __init__(self, fetch=fetch_player):
        self.fetch = fetch
        self.data: dict = {}            # joueur -> (relevé à, titres, erreur)
        self._lock = threading.Lock()
        self._running = False

    def get(self, name: str, refresh: bool = True) -> dict:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = self.data.get(name)
        if refresh and (cached is None or now - cached[0] > TTL):
            try:
                cached = (now, self.fetch(name), None)
            except Exception as e:  # pas d'internet, site injoignable…
                cached = (now - TTL + timedelta(minutes=10), cached[1] if cached else [], str(e))  # réessai dans 10 min
            self.data[name] = cached
        if cached is None:
            return {"fetched_at": None, "items": [], "error": None, "summary": None}
        return {"fetched_at": cached[0], "items": cached[1], "error": cached[2], "summary": summary(cached[1], now)}

    def prefetch(self, names: list[str], pause: float = 0.5) -> bool:
        """Relève en arrière-plan les joueurs dont les actualités datent de plus de 3 h (un seul à la fois)."""
        with self._lock:
            if self._running:
                return False
            self._running = True

        def run():
            try:
                for n in names:
                    self.get(n)
                    time.sleep(pause)
            finally:
                self._running = False

        threading.Thread(target=run, daemon=True).start()
        return True
