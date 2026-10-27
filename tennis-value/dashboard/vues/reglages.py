"""Réglages : bankroll, prudence des mises, sélection des paris (écrits dans config.yaml), données et modèle."""
from __future__ import annotations

import streamlit as st

import commun as c
from tennisvalue import sync
from tennisvalue.config import save_settings

PRUDENCE = {0.125: "Très prudent (Kelly 1/8)", 0.25: "Prudent (Kelly 1/4, conseillé)", 0.5: "Audacieux (Kelly 1/2)"}
SOURCES = {
    "sharp": "Marché sharp (conseillé) : cote juste de Pinnacle / Betfair, sans marge",
    "confirmed": "Confirmé : value exigée à la fois selon le marché sharp et selon le modèle",
    "model": "Modèle seul (déconseillé : perdant sur toutes les saisons testées)",
}

s = c.settings()
stt = sync.status(s)
st.title("Réglages")

with st.form("reglages"):
    st.subheader("Mises")
    a, b = st.columns(2)
    bankroll = a.number_input("Bankroll (€)", min_value=10.0, value=float(s.bankroll), step=50.0, format="%.0f",
                              help="Somme réservée aux paris, que tu acceptes de perdre.")
    prudence = dict(PRUDENCE)
    if s.kelly_multiplier not in prudence:
        prudence[s.kelly_multiplier] = f"Personnalisé (Kelly × {c.num(s.kelly_multiplier)})"
    kelly = b.selectbox("Prudence des mises", list(prudence), index=list(prudence).index(s.kelly_multiplier),
                        format_func=prudence.get,
                        help="Fraction de la mise de Kelly. Plus prudent = mises plus petites, bankroll plus stable.")
    a, b = st.columns(2)
    max_stake = a.slider("Mise maximale par pari (% de la bankroll)", 0.5, 5.0, 100 * s.max_stake_pct, 0.5,
                         format="%.1f %%")
    max_expo = b.slider("Total maximal en jeu en même temps (% de la bankroll)", 5, 30, round(100 * s.max_exposure_pct), 1,
                        format="%d %%")

    st.subheader("Sélection des paris")
    min_ev = st.slider("Value minimale", 1.0, 10.0, 100 * s.min_ev, 0.5, format="%.1f %%",
                       help="Un pari n'est conseillé que si son gain moyen espéré dépasse ce seuil.")
    a, b = st.columns(2)
    min_odds = a.number_input("Cote minimale", min_value=1.01, max_value=10.0, value=float(s.min_odds), step=0.05,
                              format="%.2f")
    max_odds = b.number_input("Cote maximale", min_value=1.1, max_value=50.0, value=float(s.max_odds), step=0.5,
                              format="%.2f")
    source = st.radio("Probabilité utilisée pour décider", list(SOURCES), index=list(SOURCES).index(s.decision_source),
                      format_func=SOURCES.get)

    if st.form_submit_button("💾 Enregistrer", type="primary"):
        if min_odds >= max_odds:
            st.error("La cote minimale doit être inférieure à la cote maximale.")
        else:
            save_settings({
                "bankroll": float(bankroll), "kelly_multiplier": float(kelly),
                "max_stake_pct": round(max_stake / 100, 4), "max_exposure_pct": round(max_expo / 100, 4),
                "min_ev": round(min_ev / 100, 4), "min_odds": float(min_odds), "max_odds": float(max_odds),
                "decision_source": source,
            })
            c.clear_caches()
            c.flash("Réglages enregistrés.")
            st.rerun()

st.subheader("Données et modèle")
if stt["model_ready"]:
    live = c.live_model(s, stt["model_built"])
    st.markdown(f"- Résultats de matchs jusqu'au **{live['data_until'][8:10]}/{live['data_until'][5:7]}/{live['data_until'][:4]}**\n"
                f"- Modèle calculé le **{c.local(stt['model_built']):%d/%m/%Y à %H:%M}**")
else:
    st.markdown("- Modèle pas encore calculé")
st.markdown(f"- Dernier relevé de cotes : **{c.ago(stt['last_fetch']) if stt['last_fetch'] is not None else 'aucun'}**")
if st.button("🧮 Recalculer le modèle", help="Après de nouveaux résultats de matchs (1 à 3 minutes)."):
    c.rebuild(s)

st.subheader("Relevés automatiques")
st.markdown("GitHub relève les cotes à **8 h, 11 h, 14 h, 17 h et 20 h** (heure de Paris), même ordinateur éteint. "
            "Le bouton **🔄 Mettre à jour** les récupère.")
if s.odds_api_key:
    st.markdown("- Clé The Odds API présente sur cet ordinateur : « Relever maintenant » l'utilise directement.")
elif sync.gh_ready():
    st.markdown("- GitHub CLI connecté : « Relever maintenant » lance un relevé sur GitHub (1 à 2 minutes).")
else:
    st.markdown("- Ni clé locale ni GitHub CLI : « Relever maintenant » n'est pas disponible "
                "(lancer `scripts/install_chromebook.sh`).")
st.markdown("- Historique des relevés et crédits restants : onglet **Actions** du dépôt sur GitHub.")
