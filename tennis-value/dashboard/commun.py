"""Éléments communs aux pages : réglages, formats à la française, données en cache, barre latérale."""
from __future__ import annotations

import re
from dataclasses import asdict

import pandas as pd
import streamlit as st

from tennisvalue import db, sync
from tennisvalue.config import Settings, load_settings
from tennisvalue.ingest.snapshots import import_snapshots

TZ = "Europe/Paris"
BOOKS = {
    "betclic_fr": "Betclic", "winamax_fr": "Winamax", "unibet_fr": "Unibet", "pmu_fr": "PMU",
    "parionssport_fr": "Parions Sport", "netbet_fr": "NetBet", "pinnacle": "Pinnacle",
    "betfair_ex_eu": "Betfair Exchange",
}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def settings() -> Settings:
    """Relu à chaque affichage : un changement dans Réglages s'applique tout de suite."""
    return load_settings()


# --------------------------------------------------------------------------- formats
def num(x: float, decimals: int = 2) -> str:
    return f"{x:,.{decimals}f}".replace(",", " ").replace(".", ",")


def euros(x: float) -> str:
    return num(x, 0 if float(x).is_integer() else 2) + " €"


def pct(x: float, decimals: int = 0, sign: bool = False) -> str:
    if pd.isna(x):
        return "–"
    s = num(100 * x, decimals)
    return ("+" + s if sign and x > 0 else s) + " %"


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def local(ts) -> pd.Timestamp:
    """Horodatage UTC sans fuseau (base de données) -> heure de Paris."""
    return pd.Timestamp(ts).tz_localize("UTC").tz_convert(TZ)


def when(ts) -> str:
    """« aujourd'hui 18:00 », « demain 13:30 » ou « jeu. 30/10 18:00 »."""
    t = local(ts)
    days = (t.normalize() - pd.Timestamp.now(tz=TZ).normalize()).days
    day = {0: "aujourd'hui", 1: "demain"}.get(days, f"{JOURS[t.weekday()]} {t:%d/%m}")
    return f"{day} {t:%H:%M}"


def ago(ts) -> str:
    minutes = int((now_utc() - pd.Timestamp(ts)).total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 48 * 60:
        h, m = divmod(minutes, 60)
        return f"il y a {h} h {m:02d}" if m else f"il y a {h} h"
    return f"il y a {minutes // (24 * 60)} jours"


def tournament(sport_key: str) -> str:
    """« tennis_atp_paris_masters » -> « ATP Paris Masters »."""
    words = sport_key.removeprefix("tennis_").split("_")
    return " ".join(w.upper() if w in ("atp", "wta") else w.capitalize() for w in words)


def bookmaker(key: str | None) -> str:
    return BOOKS.get(key, key or "–")


def factors(text: str, j1: str, j2: str) -> str:
    """Facteurs du scanner (« Elo global → J1 (+0.35) ») en phrase lisible."""
    if not text or text == "aucun facteur marqué":
        return "aucun facteur marquant"
    text = re.sub(r" \([+-]?\d+(\.\d+)?\)", "", text)
    return text.replace("→ J1", f"en faveur de {j1}").replace("→ J2", f"en faveur de {j2}").replace(" ; ", ", ")


# --------------------------------------------------------------------------- données
def con(s: Settings):
    return db.connect(s.db_path)


@st.cache_resource(show_spinner="Chargement du modèle…")
def live_model(_s: Settings, built_at) -> dict:
    from tennisvalue.scan import load_live

    return load_live(_s)


@st.cache_data(show_spinner="Calcul des probabilités…")
def predictions(_s: Settings, settings_key: str, last_fetch, built_at) -> pd.DataFrame:
    from tennisvalue.scan import scan

    return scan(_s, live=live_model(_s, built_at))


def upcoming_predictions(s: Settings, stt: dict) -> pd.DataFrame:
    """Matchs pas encore commencés, avec probabilités, value et mise (recalculées si cotes ou réglages changent)."""
    pred = predictions(s, repr(asdict(s)), stt["last_fetch"], stt["model_built"])
    if pred.empty:
        return pred
    return pred[pred["commence_time"] > now_utc()].sort_values("commence_time").reset_index(drop=True)


def clear_caches(model: bool = False) -> None:
    st.cache_data.clear()
    if model:
        st.cache_resource.clear()


# --------------------------------------------------------------------------- messages
def flash(msg: str, icon: str = "✅") -> None:
    """Message affiché après le rechargement de la page."""
    st.session_state["_flash"] = (msg, icon)


def show_flash() -> None:
    if "_flash" in st.session_state:
        msg, icon = st.session_state.pop("_flash")
        st.toast(msg, icon=icon)


# --------------------------------------------------------------------------- actions
def update(s: Settings) -> None:
    """Bouton « Mettre à jour » : récupère les relevés de GitHub (git pull) et les importe."""
    with st.status("Mise à jour…", expanded=True) as box:
        if sync.is_git_repo():
            box.write("Récupération des derniers relevés sur GitHub…")
            try:
                sync.git_pull()
            except sync.SyncError as e:
                box.update(label="Mise à jour impossible", state="error")
                st.error(f"Impossible de joindre GitHub (connexion internet ?). Détail : {e}")
                return
        con_ = con(s)
        n = import_snapshots(con_, s.snapshots_dir)
        con_.close()
        box.update(label="À jour", state="complete")
    clear_caches()
    flash(f"{n} nouvelle(s) cote(s) importée(s)." if n else "Aucune nouvelle cote depuis la dernière mise à jour.",
          "✅" if n else "ℹ️")
    st.rerun()


def fetch_now(s: Settings) -> None:
    """Bouton « Relever maintenant » : avec la clé locale si elle existe, sinon via GitHub Actions."""
    with st.status("Relevé des cotes…", expanded=True) as box:
        try:
            if s.odds_api_key:
                r = sync.fetch_local(s)
                msg = f"{r['n']} cotes relevées. Crédits restants : {r['remaining']}."
            elif sync.gh_ready():
                url = sync.fetch_remote(on_progress=box.write)
                box.write("Récupération du relevé…")
                sync.git_pull()
                con_ = con(s)
                n = import_snapshots(con_, s.snapshots_dir)
                con_.close()
                msg = f"{n} cotes relevées sur GitHub."
                box.markdown(f"[Voir l'exécution sur GitHub]({url})")
            else:
                box.update(label="Relevé impossible", state="error")
                st.error("Pas de clé The Odds API sur cet ordinateur, et GitHub CLI n'est pas connecté. "
                         "Lancer `scripts/install_chromebook.sh`, ou ajouter la clé dans `config.yaml`.")
                return
        except Exception as e:  # erreur réseau, clé refusée, GitHub…
            box.update(label="Relevé impossible", state="error")
            st.error(f"Le relevé a échoué : {e}")
            return
        box.update(label="Relevé terminé", state="complete")
    clear_caches()
    flash(msg)
    st.rerun()


def rebuild(s: Settings, done: str = "Modèle recalculé avec les derniers résultats.") -> None:
    """Recalcule le modèle (même chose que `tennisvalue build`) ; `done` s'affiche à la fin."""
    from tennisvalue import pipeline

    with st.status("Recalcul du modèle (1 à 3 minutes)…", expanded=True) as box:
        pipeline.build_all(s, log=box.write)
        box.update(label="Modèle recalculé", state="complete")
    clear_caches(model=True)
    flash(done)
    st.rerun()


def sidebar(s: Settings, stt: dict) -> None:
    with st.sidebar:
        st.markdown("### 🎾 Tennis Value")
        if stt["last_fetch"] is None:
            st.markdown(":orange[●] Aucun relevé de cotes")
        else:
            age_h = (now_utc() - pd.Timestamp(stt["last_fetch"])).total_seconds() / 3600
            dot = ":green[●]" if age_h < 4 else ":orange[●]" if age_h < 24 else ":red[●]"
            st.markdown(f"{dot} Cotes relevées **{ago(stt['last_fetch'])}**  \n"
                        f"{stt['upcoming']} match(s) à venir")
        if st.button("🔄 Mettre à jour", type="primary", width="stretch",
                     help="Récupère les relevés faits automatiquement par GitHub (8 h, 11 h, 14 h, 17 h, 20 h)."):
            update(s)
        if st.button("📡 Relever les cotes maintenant", width="stretch",
                     help="Nouveau relevé tout de suite (≈ 2 crédits The Odds API par tournoi ouvert)."):
            fetch_now(s)
        if stt["results_newer"]:
            st.warning("Nouveaux résultats de matchs disponibles.")
            if st.button("🧮 Recalculer le modèle", width="stretch"):
                rebuild(s)
        st.divider()
        st.caption("Aucun pari n'est sûr : une value positive est une espérance de gain sur beaucoup de paris, "
                   "jamais une garantie sur un pari donné.")
