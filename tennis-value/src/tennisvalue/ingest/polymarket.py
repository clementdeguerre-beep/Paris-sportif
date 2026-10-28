"""Marchés de prédiction Polymarket : API publiques, lecture seule, sans compte ni clé.

Polymarket publie chaque transaction (joueur, achat ou vente, montant, heure) : on y
lit de vrais flux d'argent, là où les bookmakers ne publient que leurs cotes.
Polymarket n'est pas autorisé en France : on n'y parie pas, on lit ses données.

Collecte (`tennisvalue fetch-polymarket --export`, sur GitHub Actions) : pour chaque
match ATP / WTA (marché « vainqueur du match ») commencé il y a moins de 3 h ou qui
commence dans les 72 h, un résumé (prix, volumes, liquidité) puis, depuis le relevé
précédent (ou depuis l'ouverture pour un nouveau match), les transactions et le prix
horaire du joueur 1. Un relevé = un fichier data/polymarket/AAAA-MM/AAAAMMJJTHHMMSSZ.json.gz
(jamais réécrit) ; `import-polymarket` l'ajoute à la base sans doublons.
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx
import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TENNIS_TAG = 864
OVERLAP_S = 600          # marge entre deux relevés (horloges, transactions tardives)


class PolymarketClient:
    def __init__(self, timeout: float = 30.0):
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "tennis-value"})

    def get(self, url: str, **params):
        for attempt in range(4):
            r = self.client.get(url, params=params)
            if r.status_code == 429 or r.status_code >= 500:   # limite de débit, panne passagère
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()


def _utc(s: str) -> datetime:
    """« 2026-10-28 14:30:00+00 » (format Polymarket) -> datetime UTC sans fuseau."""
    ts = pd.Timestamp(s + ":00" if s.endswith("+00") else s)
    return (ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts).to_pydatetime()


def tennis_markets(client: PolymarketClient, series=("atp", "wta"), now: datetime | None = None,
                   past_h: float = 3, horizon_h: float = 72) -> list[dict]:
    """Marchés « vainqueur du match » des circuits demandés, dans la fenêtre de temps."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    events, offset = [], 0
    while True:
        page = client.get(f"{GAMMA}/events", tag_id=TENNIS_TAG, active="true", closed="false", limit=100, offset=offset)
        events += page
        if len(page) < 100 or offset >= 5000:
            break
        offset += 100
    out = []
    for e in events:
        if e.get("seriesSlug") not in series:
            continue
        ml = next((m for m in e.get("markets") or [] if m.get("sportsMarketType") == "moneyline"), None)
        if not ml or ml.get("closed") or not ml.get("gameStartTime"):
            continue
        start = _utc(ml["gameStartTime"])
        if not (now - timedelta(hours=past_h) <= start <= now + timedelta(hours=horizon_h)):
            continue
        players, prices, tokens = (json.loads(ml[k]) for k in ("outcomes", "outcomePrices", "clobTokenIds"))
        if len(players) != 2:
            continue
        out.append({
            "cond": ml["conditionId"], "slug": e.get("slug"), "series": e.get("seriesSlug"),
            "tournament": (e.get("title") or "").split(":")[0].strip(), "game_start": start,
            "player_1": players[0], "player_2": players[1], "price_1": float(prices[0]),
            "volume": float(ml.get("volumeNum") or 0), "volume_24h": float(ml.get("volume24hr") or 0),
            "liquidity": float(ml.get("liquidityNum") or 0), "token_1": tokens[0],
        })
    return out


def trades(client: PolymarketClient, cond: str, since_ts: int = 0) -> list[dict]:
    """Transactions d'un marché depuis `since_ts` (secondes) : sans pseudonyme ni portefeuille."""
    out, offset = [], 0
    while True:
        params = {"market": cond, "limit": 500, "offset": offset}
        if since_ts:
            params["start"] = since_ts
        page = client.get(f"{DATA}/trades", **params)
        out += [{"cond": cond, "ts": int(t["timestamp"]), "outcome_index": int(t["outcomeIndex"]), "side": t["side"],
                 "usd": float(t["size"]) * float(t["price"]), "price": float(t["price"]), "tx": t.get("transactionHash")}
                for t in page]
        if len(page) < 500 or offset >= 10000:
            return out
        offset += 500


def price_history(client: PolymarketClient, cond: str, token_1: str, since_ts: int = 0) -> list[dict]:
    """Prix horaire du joueur 1 (chance estimée par le marché) depuis `since_ts`, ou depuis l'ouverture."""
    params = {"market": token_1, "fidelity": 60}
    params.update({"startTs": since_ts} if since_ts else {"interval": "max"})
    hist = client.get(f"{CLOB}/prices-history", **params).get("history", [])
    return [{"cond": cond, "ts": int(h["t"]), "price_1": float(h["p"])} for h in hist]


def collect(client: PolymarketClient, previous: dict | None = None, now: datetime | None = None,
            series=("atp", "wta")) -> dict:
    """Un relevé : marchés, et transactions / prix nouveaux depuis le relevé précédent."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    markets = tennis_markets(client, series, now)
    seen = {m["cond"] for m in (previous or {}).get("markets", [])}
    prev_ts = int(pd.Timestamp(previous["fetched_at"]).timestamp()) - OVERLAP_S if previous else 0
    all_trades, all_prices = [], []
    for m in markets:
        since = prev_ts if m["cond"] in seen else 0
        if m["volume"] > 0:
            all_trades += trades(client, m["cond"], since)
        all_prices += price_history(client, m["cond"], m["token_1"], since)
    return {"fetched_at": now.isoformat(), "markets": markets, "trades": all_trades, "prices": all_prices}


# --------------------------------------------------------------------------- fichiers
def export(snapshot: dict, folder: Path) -> Path:
    now = pd.Timestamp(snapshot["fetched_at"])
    path = folder / now.strftime("%Y-%m") / now.strftime("%Y%m%dT%H%M%SZ.json.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(snapshot, fh, default=str, ensure_ascii=False, separators=(",", ":"))
    return path


def latest(folder: Path) -> dict | None:
    files = sorted(folder.rglob("*.json.gz"))
    if not files:
        return None
    with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
        return json.load(fh)


def import_files(con: duckdb.DuckDBPyConnection, folder: Path) -> int:
    """Ajoute à la base les relevés pas encore importés ; renvoie le nombre de transactions ajoutées."""
    done = {r[0] for r in con.execute("SELECT file FROM poly_imports").fetchall()}
    added = 0
    for f in sorted(folder.rglob("*.json.gz")):
        name = f.relative_to(folder).as_posix()
        if name in done:
            continue
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            snap = json.load(fh)
        m = pd.DataFrame(snap["markets"])
        if not m.empty:
            m = m.assign(fetched_at=pd.Timestamp(snap["fetched_at"]), game_start=pd.to_datetime(m["game_start"]))
            _insert(con, "poly_markets", m[["fetched_at", "cond", "slug", "series", "tournament", "game_start",
                                            "player_1", "player_2", "price_1", "volume", "volume_24h", "liquidity"]],
                    ["fetched_at", "cond"])
        t = pd.DataFrame(snap["trades"], columns=["cond", "ts", "outcome_index", "side", "usd", "price", "tx"])
        if not t.empty:
            t["ts"] = pd.to_datetime(t["ts"], unit="s")
            added += _insert(con, "poly_trades", t, ["tx", "cond", "ts", "outcome_index", "side", "usd"])
        p = pd.DataFrame(snap["prices"], columns=["cond", "ts", "price_1"])
        if not p.empty:
            p["ts"] = pd.to_datetime(p["ts"], unit="s")
            _insert(con, "poly_prices", p.drop_duplicates(["cond", "ts"]), ["cond", "ts"])
        con.execute("INSERT INTO poly_imports VALUES (?)", [name])
    return added


def _insert(con, table: str, df: pd.DataFrame, key: list[str]) -> int:
    df = df.drop_duplicates(key)
    con.register("_new", df)    # résultats précédents lus en entier (fetchall) : exigé par DuckDB
    cond = " AND ".join(f"o.{k} IS NOT DISTINCT FROM n.{k}" for k in key)
    count = f"SELECT count(*) FROM {table}"
    before = con.execute(count).fetchall()[0][0]
    con.execute(f"INSERT INTO {table} SELECT n.* FROM _new n WHERE NOT EXISTS (SELECT 1 FROM {table} o WHERE {cond})")
    con.unregister("_new")
    return con.execute(count).fetchall()[0][0] - before
