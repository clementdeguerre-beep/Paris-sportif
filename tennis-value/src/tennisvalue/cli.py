"""Ligne de commande : `tennisvalue <commande>` (ou `python -m tennisvalue.cli <commande>`)."""
from __future__ import annotations

import json
import pickle
import subprocess
import sys
from datetime import date, datetime

import pandas as pd
import typer

from tennisvalue import db, pipeline
from tennisvalue.config import ROOT, load_settings

app = typer.Typer(help="Analyse de paris tennis : probabilités, value, mises, suivi.", no_args_is_help=True)
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)


@app.command()
def build():
    """Recalcule tout : données, notes, modèles, évaluation et backtest (≈ 1 min)."""
    s = load_settings()
    feats = pipeline.build(s)
    ev = pipeline.evaluate(feats)
    info = pipeline.information_test(feats)
    bt = pipeline.backtest(feats, s)
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "genere_le": datetime.now().isoformat(timespec="minutes"),
        "evaluation": ev, "test_information": info, "backtest": bt["summary"],
        "details": {k: {"breakdown": v["breakdown"],
                        "kelly": {kk: vv for kk, vv in v["kelly"].items()}} for k, v in bt["details"].items()},
    }
    (s.reports_dir / "backtest.json").write_text(json.dumps(pipeline.to_jsonable(payload), ensure_ascii=False, indent=1))
    with open(s.reports_dir / "backtest_details.pkl", "wb") as fh:
        pickle.dump(bt["details"], fh)
    typer.echo("\nQualité des probabilités (saisons 2019-2026, mêmes matchs) :")
    typer.echo(ev["scores"].round(4).to_string(index=False))
    cols = ["strategie", "book", "seuil", "n_paris", "yield", "ic95_bas", "ic95_haut", "clv_proxy"]
    typer.echo("\nBacktest (mises à plat) :")
    typer.echo(bt["summary"][cols].round(4).to_string(index=False))
    typer.echo(f"\nRapport : {s.reports_dir / 'backtest.json'}")


@app.command("fetch-odds")
def fetch_odds(sport: str = typer.Option("", help="Clé de tournoi précise (sinon tous les tournois tennis actifs)")):
    """Relève les cotes actuelles (ANJ + sharp) et les ajoute à l'historique horodaté."""
    from tennisvalue.ingest.oddsapi import OddsApiClient, parse_events

    s = load_settings()
    client = OddsApiClient(s.odds_api_key)
    keys = [sport] if sport else [x["key"] for x in client.tennis_sports()]
    con = db.connect(s.db_path)
    total = 0
    for k in keys:
        df = parse_events(client.odds(k, regions=s.odds_regions))
        if not df.empty:
            db.append_table(con, "odds_snapshots", df)
            total += len(df)
    con.close()
    typer.echo(f"{total} cotes enregistrées sur {len(keys)} tournoi(s). Crédits restants : {client.remaining}")


@app.command()
def scan():
    """Matchs à venir : probabilités, value, mise conseillée, alertes."""
    from tennisvalue.market.movement import stale_lines, steam_moves
    from tennisvalue.scan import latest_snapshots, load_live
    from tennisvalue.scan import scan as run_scan

    s = load_settings()
    live = load_live(s)
    lag = (date.today() - date.fromisoformat(live["data_until"])).days
    if lag > 10:
        typer.secho(f"Attention : historique arrêté au {live['data_until']} ({lag} jours). "
                    "Mettre à jour le fichier 2026 de tennis-data puis relancer `build`.", fg="yellow")
    res = run_scan(s)
    if res.empty:
        typer.echo("Aucun relevé de cotes récent : lancer d'abord `fetch-odds`.")
        raise typer.Exit()
    con = db.connect(s.db_path)
    db.write_table(con, "predictions", res.assign(scanned_at=datetime.now()))
    snaps = db.read(con, "SELECT * FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP")
    latest = latest_snapshots(con)
    con.close()
    cols = ["commence_time", "tour", "joueur_1", "joueur_2", "p_model", "p_sharp", "pari", "cote", "book",
            "value", "mise", "statut", "alerte_donnees"]
    typer.echo(res[cols].round(3).to_string(index=False))
    steam = steam_moves(snaps, s.sharp_books, s.steam_move_pp, s.steam_window_min)
    stale = stale_lines(latest, s.sharp_books, s.stale_gap_pp)
    if not steam.empty:
        typer.secho("\nMouvements brusques (sharp) :", fg="yellow")
        typer.echo(steam.round(2).to_string(index=False))
    if not stale.empty:
        typer.secho("\nCotes en retard sur le marché sharp :", fg="green")
        typer.echo(stale.round(3).to_string(index=False))
    n = int((res["statut"] == "VALUE").sum())
    typer.secho(f"\n{n} value bet(s) au-dessus du seuil de {s.min_ev:.0%}.", fg="green" if n else "white")


@app.command("bet-add")
def bet_add(match: str, selection: str, book: str, odds: float, stake: float,
            p: float = typer.Option(None, help="Proba retenue pour le pari"),
            event_id: str = typer.Option(None), tour: str = typer.Option(None), note: str = ""):
    """Enregistre un pari réellement placé."""
    from tennisvalue.tracking.bets import add_bet

    s = load_settings()
    con = db.connect(s.db_path)
    bid = add_bet(con, match=match, selection=selection, book=book, odds=odds, stake=stake,
                  p_decision=p, event_id=event_id, tour=tour, note=note)
    con.close()
    typer.echo(f"Pari n°{bid} enregistré.")


@app.command("bet-settle")
def bet_settle(bet_id: int, result: str = typer.Argument(..., help="gagne | perdu | annule")):
    """Règle un pari."""
    from tennisvalue.tracking.bets import settle_bet

    s = load_settings()
    con = db.connect(s.db_path)
    settle_bet(con, bet_id, result)
    con.close()
    typer.echo(f"Pari n°{bet_id} : {result}.")


@app.command()
def bankroll():
    """Bilan : bankroll, yield, drawdown, CLV moyenne."""
    from tennisvalue.tracking.bets import bankroll_report, update_clv

    s = load_settings()
    con = db.connect(s.db_path)
    update_clv(con, s.sharp_books)
    rep = bankroll_report(con, s.bankroll)
    con.close()
    rep.pop("bets")
    for k, v in rep.items():
        typer.echo(f"{k:15s} {v:.4f}" if isinstance(v, float) else f"{k:15s} {v}")


@app.command()
def dashboard():
    """Ouvre le tableau de bord dans le navigateur."""
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py")], check=False)


if __name__ == "__main__":
    app()
