"""Analyse : qualité du modèle face au marché et backtest des stratégies (produits par `build`)."""
from __future__ import annotations

import json
import pickle

import altair as alt
import pandas as pd
import streamlit as st

import commun as c

STRAT_LABELS = {"modele": "Modèle seul", "modele_confirme": "Modèle confirmé par le sharp", "sharp": "Sharp seul (cote en retard)"}
BOOK_LABELS = {"b365": "Bet365", "avg": "Cote moyenne", "anj_proxy": "Proxy ANJ", "max": "Meilleure cote (optimiste)", "ps": "Pinnacle"}

s = c.settings()
st.title("Analyse")
st.markdown("Ce que disent les saisons passées : **le marché sharp (Pinnacle / Betfair) prédit mieux que notre "
            "modèle**, c'est pourquoi les paris conseillés reposent sur sa cote juste. Le modèle sert de second avis.")

path = s.reports_dir / "backtest.json"
if not path.exists():
    st.info("Pas encore d'analyse : construire le modèle depuis la page Paris du jour ou Réglages.")
    st.stop()

rep = json.loads(path.read_text())
ev = rep["evaluation"]
st.caption(f"Calculée le {pd.Timestamp(rep['genere_le']):%d/%m/%Y à %H:%M} sur {c.num(ev['n_matchs'], 0)} matchs "
           f"(du {pd.Timestamp(ev['periode'][0]):%d/%m/%Y} au {pd.Timestamp(ev['periode'][1]):%d/%m/%Y}), "
           "chaque match prédit sans connaître la suite.")

st.subheader("1. Qualité des probabilités : modèle contre marché")
st.caption("Log loss et Brier : plus c'est bas, meilleures sont les probabilités.")
st.dataframe(pd.DataFrame(ev["scores"]), hide_index=True, width="stretch",
             column_config={"log_loss": st.column_config.NumberColumn(format="%.4f"),
                            "brier": st.column_config.NumberColumn(format="%.4f"),
                            "precision": st.column_config.NumberColumn("précision", format="%.3f")})
cal = pd.concat([pd.DataFrame(ev["calibration"]["modele"]).assign(source="Modèle"),
                 pd.DataFrame(ev["calibration"]["marche"]).assign(source="Marché sharp")])
line = alt.Chart(pd.DataFrame({"x": [0.5, 1], "y": [0.5, 1]})).mark_line(strokeDash=[4, 4], color="gray").encode(x="x", y="y")
pts = alt.Chart(cal).mark_line(point=True).encode(
    x=alt.X("p_moy:Q", title="Proba annoncée (favori)", scale=alt.Scale(domain=[0.5, 1])),
    y=alt.Y("freq_obs:Q", title="Fréquence de victoire observée", scale=alt.Scale(domain=[0.4, 1])),
    color=alt.Color("source:N", title=None), tooltip=["tranche", "n", "p_moy", "freq_obs", "source"])
st.altair_chart((line + pts).properties(height=320, title="Calibration : plus la courbe suit la diagonale, mieux c'est"),
                width="stretch")

st.subheader("2. Le modèle apporte-t-il une information que le marché n'a pas ?")
st.dataframe(pd.DataFrame(rep["test_information"]), hide_index=True, width="stretch")
st.caption("Si le poids du modèle est proche de 0 ou négatif, le modèle n'ajoute rien à la cote du marché.")

st.subheader("3. Stratégies de paris (mises à plat d'une unité)")
bt = pd.DataFrame(rep["backtest"])
bt["strategie"] = bt["strategie"].map(STRAT_LABELS)
bt["book"] = bt["book"].map(BOOK_LABELS)
st.dataframe(bt[["strategie", "book", "seuil", "n_paris", "reussite", "cote_moy", "yield", "ic95_bas",
                 "ic95_haut", "t_stat", "clv_proxy"]], hide_index=True, width="stretch",
             column_config={k: st.column_config.NumberColumn(format="%.3f") for k in
                            ["reussite", "cote_moy", "yield", "ic95_bas", "ic95_haut", "t_stat", "clv_proxy"]})
st.caption("Yield : bénéfice par euro misé. IC 95 % : fourchette plausible du vrai yield ; "
           "si elle contient 0, la stratégie n'est pas prouvée.")

det_path = s.reports_dir / "backtest_details.pkl"
if det_path.exists():
    with open(det_path, "rb") as fh:
        details = pickle.load(fh)
    choice = st.selectbox("Détail d'une stratégie", list(details))
    d = details[choice]
    k = d["kelly"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Bankroll finale (départ 1 000)", c.num(k["final"], 0))
    m2.metric("Baisse maximale", c.pct(k["max_dd"]))
    m3.metric("Rendement / bankroll", c.pct(k.get("roi_bankroll", 0), sign=True))
    if not k["curve"].empty:
        st.altair_chart(alt.Chart(k["curve"]).mark_line().encode(
            x=alt.X("date:T", title=None), y=alt.Y("bankroll:Q", title="Bankroll (€)")).properties(height=260),
            width="stretch")
    dim = st.radio("Découpage", ["tour", "season", "surface", "tier", "tranche_cote"], horizontal=True,
                   format_func={"tour": "Circuit", "season": "Saison", "surface": "Surface", "tier": "Catégorie",
                                "tranche_cote": "Tranche de cote"}.get)
    st.dataframe(d["breakdown"][dim], hide_index=True, width="stretch")
