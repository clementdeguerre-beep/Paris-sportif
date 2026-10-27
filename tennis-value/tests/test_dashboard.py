"""Pages de l'application, exécutées sans navigateur (streamlit.testing)."""
import sys
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from tennisvalue import db
from tennisvalue.config import ROOT
from tennisvalue.tracking.bets import add_bet

APP = str(ROOT / "dashboard" / "app.py")


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Base vide, pas de modèle : une installation qui vient de démarrer."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"db_path": str(tmp_path / "t.duckdb"), "models_dir": str(tmp_path / "models"),
                                    "reports_dir": str(tmp_path / "reports"), "raw_dir": str(tmp_path / "raw")}))
    monkeypatch.setenv("TENNISVALUE_CONFIG", str(path))
    monkeypatch.syspath_prepend(str(ROOT / "dashboard"))
    yield path
    sys.modules.pop("commun", None)


def _run(page: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    if page:
        at.switch_page(page).run()
    assert not at.exception, at.exception
    return at


def test_home_without_model(config):
    at = _run()
    assert any("modèle n'est pas encore construit" in w.value for w in at.warning)
    assert any(b.label == "🧮 Construire le modèle" for b in at.button)


def test_settle_bet_in_one_click(config):
    s = yaml.safe_load(config.read_text())
    con = db.connect(Path(s["db_path"]))
    bid = add_bet(con, match="Alcaraz - Sinner", selection="Carlos Alcaraz", book="winamax_fr", odds=2.5, stake=10)
    con.close()
    at = _run("vues/mes_paris.py")
    assert at.metric[0].value == "1\u202f000 €"
    at.button(key=f"g{bid}").click().run()
    assert not at.exception, at.exception
    assert at.metric[0].value == "1\u202f015 €"                 # 10 € à 2,50 gagnés
    assert not [b for b in at.button if b.key == f"g{bid}"]     # plus dans « En attente »


def test_settings_are_saved(config):
    at = _run("vues/reglages.py")
    at.number_input[0].set_value(500.0)
    at.get("form_submit_button")[0].click().run()
    assert not at.exception, at.exception
    assert yaml.safe_load(config.read_text())["bankroll"] == 500.0
    assert yaml.safe_load(config.read_text())["db_path"].endswith("t.duckdb")   # le reste est conservé
