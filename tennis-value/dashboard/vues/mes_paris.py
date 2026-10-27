"""Mes paris : bilan, paris en attente réglés en un clic, évolution de la bankroll, historique."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import commun as c
from tennisvalue.tracking.bets import add_bet, bankroll_report, delete_bet, settle_bet, update_clv

RESULTS = {"gagne": "✅ Gagné", "perdu": "❌ Perdu", "annule": "↩️ Annulé", "ouvert": "⏳ En attente"}

s = c.settings()
con = c.con(s)
update_clv(con, s.sharp_books)
rep = bankroll_report(con, s.bankroll)
con.close()
bets = rep["bets"]

st.title("Mes paris")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Bankroll", c.euros(rep["bankroll"]), delta=c.euros(rep["benefice"]) if rep["n_regles"] else None)
k2.metric("Rendement (yield)", c.pct(rep["yield"], 1, sign=True), help="Bénéfice / total misé sur les paris réglés.")
k3.metric("CLV moyenne", c.pct(rep["clv_moyenne"], 1, sign=True),
          help=f"Cote prise comparée à la cote juste de clôture, sur {rep['n_clv']} pari(s). "
               "Le meilleur indicateur d'un vrai avantage, bien plus fiable que les gains à court terme.")
k4.metric("En jeu", c.euros(rep["en_cours"]), help="Total des mises des paris pas encore réglés.")
if 0 < rep["n_regles"] < 300:
    st.caption(f"{rep['n_regles']} pari(s) réglé(s) : trop peu pour conclure. Il en faut plusieurs centaines ; "
               "en attendant, suivre surtout la CLV.")


def settle(bet_id: int, result: str) -> None:
    con_ = c.con(s)
    settle_bet(con_, bet_id, result)
    con_.close()
    c.flash(f"Pari n°{bet_id} : {RESULTS[result]}.")


def remove(bet_id: int) -> None:
    con_ = c.con(s)
    delete_bet(con_, bet_id)
    con_.close()
    c.flash(f"Pari n°{bet_id} supprimé.", "🗑️")


st.subheader("En attente de résultat")
open_bets = bets[bets["status"] == "ouvert"] if not bets.empty else bets
if open_bets.empty:
    st.caption("Aucun pari en attente. Les paris enregistrés depuis « Paris du jour » apparaissent ici.")
for b in open_bets.itertuples():
    with st.container(border=True):
        info, g, p, a, d = st.columns([5, 1.2, 1.2, 1.2, 0.6], vertical_alignment="center")
        # placed_at est en heure locale (datetime.now() dans add_bet).
        info.markdown(f"**{b.selection}** à {c.num(b.odds)} · {c.euros(b.stake)} chez {c.bookmaker(b.book)}  \n"
                      f"{b.match} · placé le {pd.Timestamp(b.placed_at):%d/%m à %H:%M}")
        g.button("✅ Gagné", key=f"g{b.bet_id}", on_click=settle, args=(b.bet_id, "gagne"), width="stretch")
        p.button("❌ Perdu", key=f"p{b.bet_id}", on_click=settle, args=(b.bet_id, "perdu"), width="stretch")
        a.button("↩️ Annulé", key=f"a{b.bet_id}", on_click=settle, args=(b.bet_id, "annule"), width="stretch",
                 help="Match non joué ou abandon remboursé : mise rendue.")
        with d.popover("", icon=":material/delete:", help="Supprimer (pari saisi par erreur)"):
            st.button("Confirmer la suppression", key=f"d{b.bet_id}", on_click=remove, args=(b.bet_id,), type="primary")

settled = bets[bets["status"].isin(["gagne", "perdu", "annule"])] if not bets.empty else bets
if not settled.empty:
    st.subheader("Évolution de la bankroll")
    curve = pd.DataFrame({
        "Pari": range(0, len(settled) + 1),
        "Bankroll (€)": [s.bankroll] + (s.bankroll + settled["pnl"].cumsum()).tolist(),
    })
    st.altair_chart(alt.Chart(curve).mark_line(point=True, color="#1F7A4D").encode(
        x=alt.X("Pari:Q", title="Paris réglés", axis=alt.Axis(tickMinStep=1, format="d")),
        y=alt.Y("Bankroll (€):Q", scale=alt.Scale(zero=False), axis=alt.Axis(format="d")),
        tooltip=["Pari", "Bankroll (€)"]).properties(height=260), width="stretch")

if not bets.empty:
    st.subheader("Historique")
    hist = bets.sort_values("placed_at", ascending=False)
    st.dataframe(pd.DataFrame({
        "N°": hist["bet_id"],
        "Date": hist["placed_at"],
        "Match": hist["match"],
        "Pari": hist["selection"],
        "Bookmaker": hist["book"].map(c.bookmaker),
        "Cote": hist["odds"].map(c.num),
        "Mise": hist["stake"].map(c.euros),
        "Résultat": hist["status"].map(RESULTS),
        "Gain": ["" if pd.isna(g) else ("+" if g > 0 else "") + c.euros(g) for g in hist["pnl"]],
        "CLV": ["" if pd.isna(v) else c.pct(v, 1, sign=True) for v in hist["clv"]],
    }), hide_index=True, width="stretch", column_config={
        "Date": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm"),
        "CLV": st.column_config.TextColumn(help="Cote prise comparée à la cote juste de clôture (connue après le début du match)."),
    })

with st.expander("➕ Ajouter un pari à la main"):
    with st.form("ajout", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        match = c1.text_input("Match", placeholder="Alcaraz - Sinner")
        selection = c2.text_input("Joueur parié", placeholder="Carlos Alcaraz")
        book = c3.selectbox("Bookmaker", list(s.anj_books) + ["autre"], format_func=c.bookmaker)
        c4, c5 = st.columns(2)
        odds = c4.number_input("Cote", min_value=1.01, value=2.0, step=0.01, format="%.2f")
        stake = c5.number_input("Mise (€)", min_value=0.5, value=10.0, step=0.5, format="%.2f")
        st.caption("Pour la CLV automatique, enregistrer plutôt le pari depuis « Paris du jour ».")
        if st.form_submit_button("Enregistrer", type="primary"):
            if match and selection:
                con_ = c.con(s)
                add_bet(con_, match=match, selection=selection, book=book, odds=odds, stake=stake)
                con_.close()
                c.flash("Pari enregistré.")
                st.rerun()
            else:
                st.error("Renseigner le match et le joueur parié.")
