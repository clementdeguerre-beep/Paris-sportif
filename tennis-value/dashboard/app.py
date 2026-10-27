"""Tableau de bord : `tennisvalue dashboard` (ou `streamlit run dashboard/app.py`)."""
from __future__ import annotations

import json
import pickle

import altair as alt
import pandas as pd
import streamlit as st

from tennisvalue import db
from tennisvalue.config import load_settings

st.set_page_config(page_title="Tennis Value", page_icon="🎾", layout="wide")
S = load_settings()

STRAT_LABELS = {"modele": "Modèle seul", "modele_confirme": "Modèle confirmé par le sharp", "sharp": "Sharp seul (cote en retard)"}
BOOK_LABELS = {"b365": "Bet365", "avg": "Cote moyenne", "anj_proxy": "Proxy ANJ", "max": "Meilleure cote (optimiste)", "ps": "Pinnacle"}


def _con():
    return db.connect(S.db_path)


def _table_exists(con, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


st.title("🎾 Tennis Value")
st.caption("Probabilités, value bets et suivi de bankroll · ATP / WTA · Aucun pari n'est sûr : "
           "la value est une espérance, pas une garantie.")

tab_today, tab_bt, tab_bets = st.tabs(["Matchs du jour", "Backtest", "Mes paris"])

# --------------------------------------------------------------------------- Matchs du jour
with tab_today:
    con = _con()
    if st.button("Relever les cotes et scanner", help="Consomme des crédits The Odds API"):
        if not S.odds_api_key:
            st.error("Clé The Odds API absente : définir ODDS_API_KEY ou odds_api_key dans config.yaml.")
        else:
            from tennisvalue.ingest.oddsapi import OddsApiClient, parse_events
            from tennisvalue.scan import scan

            client = OddsApiClient(S.odds_api_key)
            with st.spinner("Relevé des cotes…"):
                for sp in client.tennis_sports():
                    df = parse_events(client.odds(sp["key"], regions=S.odds_regions))
                    if not df.empty:
                        db.append_table(con, "odds_snapshots", df)
                con.close()
                res = scan(S)
                con = _con()
                if not res.empty:
                    db.write_table(con, "predictions", res.assign(scanned_at=pd.Timestamp.now()))
            st.success(f"Cotes relevées. Crédits restants : {client.remaining}")

    if not _table_exists(con, "predictions"):
        st.info("Aucun scan pour l'instant. Lancer `tennisvalue fetch-odds` puis `tennisvalue scan`, "
                "ou le bouton ci-dessus.")
    else:
        pred = db.read(con, "SELECT * FROM predictions")
        c1, c2, c3 = st.columns(3)
        tours = c1.multiselect("Circuit", sorted(pred["tour"].unique()), default=sorted(pred["tour"].unique()))
        only_value = c2.toggle("Seulement les value bets", value=True)
        c3.metric("Value bets", int((pred["statut"] == "VALUE").sum()), help=f"Seuil : {S.min_ev:.0%}")
        view = pred[pred["tour"].isin(tours)]
        if only_value:
            view = view[view["statut"] == "VALUE"]
        st.caption(f"Proba de décision : **{S.decision_source}** · scan du {pred['scanned_at'].max():%d/%m %H:%M}")
        st.dataframe(
            view[["commence_time", "tour", "tournoi", "joueur_1", "joueur_2", "p_model", "p_sharp", "pari",
                  "cote", "book", "value", "mise", "facteurs_cles", "alerte_donnees"]],
            hide_index=True, width="stretch",
            column_config={
                "commence_time": st.column_config.DatetimeColumn("Début (UTC)", format="DD/MM HH:mm"),
                "p_model": st.column_config.NumberColumn("P modèle J1", format="percent", help="Proba du modèle que J1 gagne"),
                "p_sharp": st.column_config.NumberColumn("P marché J1", format="percent", help="Pinnacle / Betfair sans marge"),
                "value": st.column_config.NumberColumn("Value", format="percent"),
                "mise": st.column_config.NumberColumn("Mise conseillée", format="%.2f €"),
                "cote": st.column_config.NumberColumn("Meilleure cote ANJ", format="%.2f"),
                "facteurs_cles": st.column_config.TextColumn("Facteurs clés (modèle)", width="large"),
            },
        )
        if _table_exists(con, "odds_snapshots"):
            from tennisvalue.market.movement import open_close, stale_lines, steam_moves
            from tennisvalue.scan import latest_snapshots

            snaps = db.read(con, "SELECT * FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP")
            if not snaps.empty:
                st.subheader("Mouvements de cotes")
                steam = steam_moves(snaps, S.sharp_books, S.steam_move_pp, S.steam_window_min)
                if steam.empty:
                    st.write("Aucun mouvement brusque détecté sur les cotes sharp.")
                else:
                    st.warning("Mouvements brusques : vérifier l'actualité (forfait, blessure) avant d'agir.")
                    st.dataframe(steam, hide_index=True, width="stretch")
                stale = stale_lines(latest_snapshots(con), S.sharp_books, S.stale_gap_pp)
                st.write("**Cotes en retard sur le marché sharp**")
                st.dataframe(stale, hide_index=True, width="stretch")
                with st.expander("Ouverture → clôture par bookmaker"):
                    st.dataframe(open_close(snaps), hide_index=True, width="stretch")
    con.close()

# --------------------------------------------------------------------------- Backtest
with tab_bt:
    path = S.reports_dir / "backtest.json"
    if not path.exists():
        st.info("Lancer `tennisvalue build` pour produire le backtest.")
    else:
        rep = json.loads(path.read_text())
        ev = rep["evaluation"]
        st.subheader("1. Qualité des probabilités : modèle vs marché")
        st.caption(f"{ev['n_matchs']:,} matchs, du {ev['periode'][0]} au {ev['periode'][1]}, prédits sans voir le futur. "
                   "Log loss et Brier : plus bas = meilleur.")
        scores = pd.DataFrame(ev["scores"])
        st.dataframe(scores, hide_index=True, width="stretch",
                     column_config={"log_loss": st.column_config.NumberColumn(format="%.4f"),
                                    "brier": st.column_config.NumberColumn(format="%.4f"),
                                    "precision": st.column_config.NumberColumn("précision", format="%.3f")})
        cal = pd.concat([pd.DataFrame(ev["calibration"]["modele"]).assign(source="Modèle"),
                         pd.DataFrame(ev["calibration"]["marche"]).assign(source="Marché sharp")])
        line = alt.Chart(pd.DataFrame({"x": [0.5, 1], "y": [0.5, 1]})).mark_line(strokeDash=[4, 4], color="gray").encode(x="x", y="y")
        pts = alt.Chart(cal).mark_line(point=True).encode(
            x=alt.X("p_moy:Q", title="Proba annoncée (favori)", scale=alt.Scale(domain=[0.5, 1])),
            y=alt.Y("freq_obs:Q", title="Fréquence de victoire observée", scale=alt.Scale(domain=[0.4, 1])),
            color="source:N", tooltip=["tranche", "n", "p_moy", "freq_obs", "source"])
        st.altair_chart((line + pts).properties(height=320, title="Courbe de calibration"), width="stretch")

        st.subheader("2. Le modèle apporte-t-il une info que le marché n'a pas ?")
        st.dataframe(pd.DataFrame(rep["test_information"]), hide_index=True, width="stretch")
        st.caption("Si le poids du modèle est proche de 0 ou négatif, le modèle n'ajoute rien à la cote du marché.")

        st.subheader("3. Stratégies de paris (mises à plat de 1 unité)")
        bt = pd.DataFrame(rep["backtest"])
        bt["strategie"] = bt["strategie"].map(STRAT_LABELS)
        bt["book"] = bt["book"].map(BOOK_LABELS)
        st.dataframe(bt[["strategie", "book", "seuil", "n_paris", "reussite", "cote_moy", "yield", "ic95_bas",
                         "ic95_haut", "t_stat", "clv_proxy"]], hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.3f") for c in
                                    ["reussite", "cote_moy", "yield", "ic95_bas", "ic95_haut", "t_stat", "clv_proxy"]})
        det_path = S.reports_dir / "backtest_details.pkl"
        if det_path.exists():
            details = pickle.load(open(det_path, "rb"))
            choice = st.selectbox("Détail d'une stratégie", list(details))
            d = details[choice]
            k = d["kelly"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Bankroll finale (départ 1 000)", f"{k['final']:,.0f}")
            c2.metric("Drawdown max", f"{k['max_dd']:.0%}")
            c3.metric("Rendement / bankroll", f"{k.get('roi_bankroll', 0):+.0%}")
            if not k["curve"].empty:
                st.altair_chart(alt.Chart(k["curve"]).mark_line().encode(
                    x=alt.X("date:T", title=None), y=alt.Y("bankroll:Q", title="Bankroll (€)")).properties(height=260),
                    width="stretch")
            dim = st.radio("Découpage", ["tour", "season", "surface", "tier", "tranche_cote"], horizontal=True)
            st.dataframe(d["breakdown"][dim], hide_index=True, width="stretch")

# --------------------------------------------------------------------------- Mes paris
with tab_bets:
    from tennisvalue.tracking.bets import add_bet, bankroll_report, settle_bet, update_clv

    con = _con()
    with st.form("add_bet", clear_on_submit=True):
        st.write("**Enregistrer un pari placé**")
        c1, c2, c3 = st.columns(3)
        match = c1.text_input("Match", placeholder="Alcaraz - Sinner")
        selection = c2.text_input("Joueur parié")
        book = c3.selectbox("Bookmaker", list(S.anj_books) + ["autre"])
        c4, c5, c6 = st.columns(3)
        odds = c4.number_input("Cote", min_value=1.01, value=2.0, step=0.01)
        stake = c5.number_input("Mise (€)", min_value=0.5, value=10.0, step=0.5)
        p = c6.number_input("Proba retenue (optionnel)", min_value=0.0, max_value=1.0, value=0.0, step=0.01)
        event_id = st.text_input("Identifiant du match (event_id, pour la CLV automatique)", value="")
        if st.form_submit_button("Enregistrer") and match and selection:
            add_bet(con, match=match, selection=selection, book=book, odds=odds, stake=stake,
                    p_decision=p or None, event_id=event_id or None)
            st.success("Pari enregistré.")

    update_clv(con, S.sharp_books)
    rep = bankroll_report(con, S.bankroll)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bankroll", f"{rep['bankroll']:,.2f} €")
    c2.metric("Yield", "–" if pd.isna(rep["yield"]) else f"{rep['yield']:+.1%}")
    c3.metric("CLV moyenne", "–" if pd.isna(rep["clv_moyenne"]) else f"{rep['clv_moyenne']:+.1%}",
              help=f"Sur {rep['n_clv']} pari(s). Indicateur le plus fiable de l'avantage réel.")
    c4.metric("Drawdown max", f"{rep['drawdown_max']:.0%}")
    if rep["n_regles"] and rep["n_regles"] < 300:
        st.caption(f"{rep['n_regles']} paris réglés : échantillon trop petit pour conclure "
                   "(il faut plusieurs centaines de paris ; suivre surtout la CLV).")
    bets = rep["bets"]
    if not bets.empty:
        open_bets = bets[bets["status"] == "ouvert"]
        if not open_bets.empty:
            c1, c2, c3 = st.columns([2, 1, 1])
            bid = c1.selectbox("Régler le pari", open_bets["bet_id"],
                               format_func=lambda i: f"n°{i} · {open_bets.set_index('bet_id').loc[i, 'match']}")
            res = c2.selectbox("Résultat", ["gagne", "perdu", "annule"])
            if c3.button("Valider"):
                settle_bet(con, int(bid), res)
                st.rerun()
        st.dataframe(bets, hide_index=True, width="stretch")
    con.close()
