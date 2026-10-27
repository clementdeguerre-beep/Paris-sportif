"""Application : `tennisvalue dashboard`, l'icône Tennis Value, ou `streamlit run dashboard/app.py`."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

import commun
from tennisvalue import sync

st.set_page_config(page_title="Tennis Value", page_icon=str(Path(__file__).parent / "icone.png"), layout="wide")

s = commun.settings()
stt = sync.status(s)
commun.sidebar(s, stt)
commun.show_flash()

pages = st.navigation([
    st.Page("vues/aujourdhui.py", title="Paris du jour", icon="🎾", default=True),
    st.Page("vues/mes_paris.py", title="Mes paris", icon="📒"),
    st.Page("vues/analyse.py", title="Analyse", icon="📊"),
    st.Page("vues/reglages.py", title="Réglages", icon="⚙️"),
], position="top")
pages.run()
