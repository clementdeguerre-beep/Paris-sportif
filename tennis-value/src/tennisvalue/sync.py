"""Mise à jour des données, depuis l'application ou la ligne de commande.

- fetch_local  : relevé des cotes avec la clé locale (config.yaml ou ODDS_API_KEY) ;
- fetch_remote : lance le workflow GitHub Actions « Relevé des cotes » (la clé reste
  un secret GitHub) avec GitHub CLI (`gh`), puis attend la fin ;
- git_pull     : récupère les relevés et résultats ajoutés au dépôt par GitHub Actions ;
- status       : date du dernier relevé, matchs à venir, modèle plus ancien que les résultats.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone

import pandas as pd

from tennisvalue import db
from tennisvalue.config import ROOT, Settings

WORKFLOW = "fetch-odds.yml"


class SyncError(RuntimeError):
    """Erreur dont le message peut être montré tel quel à l'utilisateur."""


def fetch_local(settings: Settings, sport: str | None = None, export: bool = False) -> dict:
    """Relève les cotes de tous les tournois actifs (ou d'un seul) et les ajoute à odds_snapshots."""
    from tennisvalue.ingest.oddsapi import OddsApiClient, parse_events
    from tennisvalue.ingest.snapshots import export_snapshot

    client = OddsApiClient(settings.odds_api_key)
    keys = [sport] if sport else [x["key"] for x in client.tennis_sports()]
    con = db.connect(settings.db_path)
    frames = []
    for k in keys:
        df = parse_events(client.odds(k, regions=settings.odds_regions))
        if not df.empty:
            db.append_table(con, "odds_snapshots", df)
            frames.append(df)
    con.close()
    path = export_snapshot(pd.concat(frames), settings.snapshots_dir) if export and frames else None
    return {"n": sum(len(df) for df in frames), "n_tournois": len(keys), "remaining": client.remaining,
            "export": path}


def _run(cmd: list[str], timeout: float = 120) -> str:
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SyncError(f"Commande introuvable : {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise SyncError(f"Pas de réponse au bout de {timeout:.0f} s : {' '.join(cmd)}") from None
    if r.returncode != 0:
        raise SyncError((r.stderr or r.stdout).strip() or f"Échec : {' '.join(cmd)}")
    return r.stdout


def is_git_repo() -> bool:
    try:
        return _run(["git", "rev-parse", "--is-inside-work-tree"], timeout=20).strip() == "true"
    except SyncError:
        return False


def git_pull() -> bool:
    """Récupère les nouveautés du dépôt ; True si quelque chose a changé."""
    before = _run(["git", "rev-parse", "HEAD"]).strip()
    _run(["git", "pull", "-q", "--ff-only"])
    return _run(["git", "rev-parse", "HEAD"]).strip() != before


def gh_ready() -> bool:
    """GitHub CLI installé et connecté (installé par scripts/install_chromebook.sh)."""
    if shutil.which("gh") is None:
        return False
    try:
        _run(["gh", "auth", "status"], timeout=20)
    except SyncError:
        return False
    return True


def fetch_remote(on_progress=lambda msg: None, timeout: float = 600, poll: float = 5) -> str:
    """Lance le relevé sur GitHub Actions et attend la fin ; renvoie le lien vers l'exécution."""
    started = pd.Timestamp(datetime.now(timezone.utc)) - pd.Timedelta(seconds=30)  # marge d'horloge
    _run(["gh", "workflow", "run", WORKFLOW])
    on_progress("Relevé demandé à GitHub…")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll)
        runs = json.loads(_run(["gh", "run", "list", "--workflow", WORKFLOW, "--event", "workflow_dispatch",
                                "--limit", "5", "--json", "status,conclusion,createdAt,url"]))
        runs = [r for r in runs if pd.Timestamp(r["createdAt"]) >= started]
        if not runs:
            continue
        run = runs[0]
        if run["status"] != "completed":
            on_progress("Relevé en cours sur GitHub…" if run["status"] == "in_progress" else "En file d'attente sur GitHub…")
            continue
        if run["conclusion"] != "success":
            raise SyncError(f"Le relevé a échoué sur GitHub : {run['url']}")
        return run["url"]
    raise SyncError("Le relevé GitHub n'est pas terminé au bout de 10 minutes : réessayer plus tard.")


def status(settings: Settings) -> dict:
    """État des données pour l'en-tête de l'application (horodatages en UTC)."""
    con = db.connect(settings.db_path)
    last_fetch, upcoming = con.execute(
        "SELECT max(fetched_at), count(DISTINCT event_id) FILTER (WHERE commence_time > now()::TIMESTAMP) "
        "FROM odds_snapshots").fetchone()
    con.close()
    model = settings.models_dir / "live_model.pkl"
    model_time = model.stat().st_mtime if model.exists() else None
    raw_time = max((p.stat().st_mtime for p in settings.raw_dir.rglob("*.xlsx")), default=None)
    return {
        "last_fetch": last_fetch,
        "upcoming": int(upcoming or 0),
        "model_ready": model_time is not None,
        "model_built": datetime.fromtimestamp(model_time, timezone.utc).replace(tzinfo=None) if model_time else None,
        "results_newer": bool(model_time and raw_time and raw_time > model_time + 60),
    }
