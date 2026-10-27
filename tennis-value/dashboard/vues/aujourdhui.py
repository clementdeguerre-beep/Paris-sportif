"""Paris du jour : value bets en cartes, « J'ai placé ce pari » en un clic, autres matchs à venir."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import commun as c
from tennisvalue import db, sync
from tennisvalue.market.movement import steam_moves
from tennisvalue.market.staking import StakingRules, expected_value, scale_to_exposure
from tennisvalue.tracking.bets import add_bet

s = c.settings()
stt = sync.status(s)

st.title("Paris du jour")

if not stt["model_ready"]:
    st.warning("Le modèle n'est pas encore construit : il faut le calculer une fois (1 à 3 minutes).")
    if st.button("🧮 Construire le modèle", type="primary"):
        c.rebuild(s)
    st.stop()

pred = c.upcoming_predictions(s, stt)
data_until = pd.Timestamp(c.live_model(s, stt["model_built"])["data_until"])
if (pd.Timestamp.now().normalize() - data_until).days > 10:
    st.warning(f"Résultats de matchs arrêtés au {data_until:%d/%m/%Y} : les avis du modèle ne tiennent pas compte "
               "des derniers matchs. Ils se mettent à jour chaque lundi (🔄 Mettre à jour, puis Recalculer le modèle).")
if pred.empty:
    st.info("Aucun match à venir dans les relevés de cotes. Clique sur **🔄 Mettre à jour** à gauche ; "
            "s'il n'y a toujours rien, aucun tournoi ATP / WTA couvert n'est ouvert aux paris en ce moment.")
    st.stop()

con = c.con(s)
bets = db.read(con, "SELECT * FROM bets")
snaps = db.read(con, "SELECT * FROM odds_snapshots WHERE commence_time > now()::TIMESTAMP")
con.close()

# Mises : Kelly fractionné, plafond d'exposition (paris déjà en cours compris), arrondi à 0,50 €.
value = pred[pred["statut"] == "VALUE"].copy()
open_total = float(bets.loc[bets["status"] == "ouvert", "stake"].sum())
rules = StakingRules(s.kelly_multiplier, s.max_stake_pct, s.max_exposure_pct)
value["mise"] = [round(2 * m) / 2 for m in scale_to_exposure(value["mise"].tolist(), s.bankroll, rules, open_total)]
value["p_pari"] = [p if pari == j1 else 1 - p for p, pari, j1 in zip(value["p_decision"], value["pari"], value["joueur_1"])]
bet_events = set(bets["event_id"].dropna())
steam = steam_moves(snaps, s.sharp_books, s.steam_move_pp, s.steam_window_min) if not snaps.empty else pd.DataFrame()
steam_events = set(steam["event_id"]) if not steam.empty else set()

k1, k2, k3 = st.columns(3)
k1.metric("Matchs à venir", len(pred))
k2.metric("Paris conseillés", len(value), help=f"Value d'au moins {c.pct(s.min_ev)} selon la cote juste du marché sharp.")
k3.metric("Mises conseillées", c.euros(value["mise"].sum()),
          help=f"Plafond : {c.pct(s.max_exposure_pct)} de la bankroll engagés en même temps, paris en cours compris "
               f"({c.euros(open_total)} actuellement).")


@st.dialog("Enregistrer mon pari")
def record_bet(row: pd.Series, stake: float) -> None:
    st.markdown(f"**{row['pari']}** · {row['joueur_1']} – {row['joueur_2']}  \n{c.when(row['commence_time'])}")
    books = list(s.anj_books)
    book = st.selectbox("Bookmaker", books, index=books.index(row["book"]) if row["book"] in books else 0,
                        format_func=c.bookmaker)
    odds = st.number_input("Cote obtenue", min_value=1.01, value=float(row["cote"]), step=0.01, format="%.2f",
                           help="Modifier si la cote a bougé au moment de parier.")
    amount = st.number_input("Mise (€)", min_value=0.5, value=float(max(stake, 0.5)), step=0.5, format="%.2f")
    ev = expected_value(row["p_pari"], odds)
    if ev >= s.min_ev:
        st.caption(f"Value à cette cote : **{c.pct(ev, 1, sign=True)}**")
    else:
        st.warning(f"À cette cote, la value n'est plus que de {c.pct(ev, 1, sign=True)} "
                   f"(seuil {c.pct(s.min_ev)}) : le pari n'est plus conseillé.")
    if st.button("Enregistrer", type="primary", width="stretch"):
        con_ = c.con(s)
        add_bet(con_, match=f"{row['joueur_1']} - {row['joueur_2']}", selection=row["pari"], book=book, odds=odds,
                stake=amount, p_decision=float(row["p_pari"]), event_id=row["event_id"], tour=row["tour"])
        con_.close()
        c.flash(f"Pari enregistré : {c.euros(amount)} sur {row['pari']} à {c.num(odds)}.")
        st.rerun()


st.subheader("Paris conseillés")
if value.empty:
    st.info(f"Aucun pari au-dessus du seuil de value ({c.pct(s.min_ev)}) pour l'instant. "
            "Les cotes bougent : revenir après le prochain relevé.")
for _, row in value.sort_values("commence_time").iterrows():
    with st.container(border=True):
        st.markdown(f"**{row['joueur_1']} – {row['joueur_2']}** · {c.tournament(row['tournoi'])} · "
                    f"🕒 {c.when(row['commence_time'])}")
        st.markdown(f"#### 👉 {row['pari']} à {c.num(row['cote'])} chez {c.bookmaker(row['book'])}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Mise conseillée", c.euros(row["mise"]))
        m2.metric("Value", c.pct(row["value"], 1, sign=True), help="Gain moyen espéré par euro misé.")
        m3.metric("Chance de gagner", c.pct(row["p_pari"]), help="Selon la cote juste de Pinnacle / Betfair (sans marge).")
        p_model = row["p_model"] if row["pari"] == row["joueur_1"] else 1 - row["p_model"]
        m4.metric("Selon notre modèle", c.pct(p_model), help="Second avis : le modèle seul ne bat pas le marché.")
        st.caption(f"Facteurs du modèle : {c.factors(row['facteurs_cles'], row['joueur_1'], row['joueur_2'])}")
        if row["event_id"] in steam_events:
            st.warning("⚡ La cote sharp a bougé brusquement : vérifier l'actualité (forfait, blessure) avant de parier.")
        if row["alerte_donnees"]:
            st.caption(f"⚠️ {row['alerte_donnees'].strip(' ;')}")
        if row["event_id"] in bet_events:
            st.success("Pari déjà enregistré pour ce match (voir Mes paris).")
        elif st.button("✅ J'ai placé ce pari", key=f"pari_{row['event_id']}", type="primary"):
            record_bet(row, row["mise"])

st.subheader("Tous les matchs à venir")


def odds_pair(o1: float, o2: float) -> str:
    return " / ".join("–" if pd.isna(o) else c.num(o) for o in (o1, o2))


table = pd.DataFrame({
    "Début": [c.when(t) for t in pred["commence_time"]],
    "Tournoi": [c.tournament(k) for k in pred["tournoi"]],
    "Match": pred["joueur_1"] + " – " + pred["joueur_2"],
    "Marché": 100 * pred["p_sharp"],
    "Modèle": 100 * pred["p_model"],
    "Cotes ANJ": [odds_pair(a, b) for a, b in zip(pred["cote_anj_1"], pred["cote_anj_2"])],
    "Conseil": ["✅ " + p if st_ == "VALUE" else "" for p, st_ in zip(pred["pari"], pred["statut"])],
    "Remarque": pred["alerte_donnees"].str.strip(" ;"),
})
chance = dict(format="%.0f %%", min_value=0, max_value=100, width="small")
st.dataframe(table, hide_index=True, width="stretch", column_config={
    "Marché": st.column_config.ProgressColumn(help="Chance que le 1er joueur du match gagne, selon le marché sharp.", **chance),
    "Modèle": st.column_config.ProgressColumn(help="Chance que le 1er joueur du match gagne, selon notre modèle.", **chance),
    "Cotes ANJ": st.column_config.TextColumn(help="Meilleure cote ANJ pour le 1er / le 2e joueur.", width="small"),
})

if not steam.empty:
    with st.expander(f"⚡ Mouvements brusques de cotes ({len(steam)})"):
        for m in steam.itertuples():
            who = m.player_1 if m.move_pp_1 > 0 else m.player_2
            st.markdown(f"- **{m.player_1} – {m.player_2}** : la chance de **{who}** a gagné "
                        f"{c.num(abs(m.move_pp_1), 1)} points en {m.minutes:.0f} min chez {c.bookmaker(m.book)} "
                        f"({c.when(m.at)}).")
