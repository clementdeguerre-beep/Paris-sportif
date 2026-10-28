"""Profil des joueurs (forme, surface, tournoi, physique, mental), mes cotes et verdicts du scanner."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import make_live
from tennisvalue.config import Settings
from tennisvalue.models.explain import GROUPS
from tennisvalue.models.features import FEATURE_VERSION, FEATURES
from tennisvalue.models.profile import PlayerProfile, live_tournament_key, set_stats, tournament_key


def _m(d, w, l, sets, best_of=3, comment="Completed", surface="Hard", loc="Paris", tier="Masters 1000", ranks=(10, 20)):
    r = {"date": d, "winner_key": w, "loser_key": l, "best_of": best_of, "surface": surface, "location": loc,
         "tier": tier, "comment": comment, "completed": comment == "Completed", "walkover": comment == "Walkover",
         "w_rank": ranks[0], "l_rank": ranks[1]}
    for i in range(1, 6):
        ws, ls = sets[i - 1] if i <= len(sets) else (np.nan, np.nan)
        r[f"w{i}"], r[f"l{i}"] = ws, ls
    r["w_games"] = float(np.nansum([s[0] for s in sets]))
    r["l_games"] = float(np.nansum([s[1] for s in sets]))
    return SimpleNamespace(**r)


def test_set_stats_tiebreaks_decider_first_set():
    s = set_stats(_m(date(2026, 1, 1), "a", "b", [(6, 7), (7, 6), (7, 6)]))
    assert s == {"sw": 2, "sl": 1, "tbw": 2, "tbl": 1, "dec": 1, "first": -1}
    s = set_stats(_m(date(2026, 1, 1), "a", "b", [(6, 3), (6, 4)]))
    assert (s["dec"], s["first"], s["tbw"] + s["tbl"]) == (0, 1, 0)
    s = set_stats(_m(date(2026, 1, 1), "a", "b", [(6, 4), (3, 6), (6, 4), (4, 6), (13, 12)], best_of=5))
    assert (s["tbw"], s["dec"]) == (1, 1)                                      # super tie-break du 5e set


def test_tournament_key_and_live_resolution():
    assert tournament_key("Paris", "Hard", "Masters 1000") == "paris|Hard|tour"
    assert tournament_key("Paris", "Clay", "Grand Slam") == "paris|Clay|slam"
    assert tournament_key("Toronto", "Hard", "Masters 1000") == tournament_key("Montreal", "Hard", "WTA1000")
    known = {"paris|Hard|tour", "paris|Clay|slam", "london|Grass|slam", "basel|Hard|tour", "wuhan|Hard|tour"}
    assert live_tournament_key("tennis_atp_paris_masters", "Hard", "Other", known) == "paris|Hard|tour"
    assert live_tournament_key("tennis_atp_french_open", "Clay", "Grand Slam", known) == "paris|Clay|slam"
    assert live_tournament_key("tennis_atp_wimbledon", "Grass", "Grand Slam", known) == "london|Grass|slam"
    assert live_tournament_key("tennis_atp_swiss_indoors", "Hard", "Other", known) == "basel|Hard|tour"
    assert live_tournament_key("tennis_wta_wuhan_open", "Hard", "Other", known) == "wuhan|Hard|tour"
    assert live_tournament_key("tennis_atp_inconnu", "Hard", "Other", known) is None


def test_profile_form_surface_tournament_injury_break():
    pr = PlayerProfile()
    d0 = date(2026, 1, 5)
    tk = tournament_key("Paris", "Hard", "Masters 1000")
    for k in range(8):                     # a gagne 8 matchs (attendus à 50 % par l'Elo), tie-break et 3e set compris
        pr.record(_m(d0 + timedelta(days=k), "a", f"x{k}", [(7, 6), (4, 6), (6, 4)]), 0.5, tk)
    pr.record(_m(d0 + timedelta(days=9), "b", "a", [(6, 1)], comment="Retired"), 0.5, tk)   # a abandonne
    s = pr.stats("a", d0 + timedelta(days=12), "Hard", tk)
    assert (s["n10"], s["w10"], s["form10"]) == (8, 8, "VVVVVVVV") and s["surplus10"] == pytest.approx(4.0)
    assert (s["surf_n"], s["tourn_n"], s["tourn_w"], s["tb_w"], s["tb_l"], s["dec_w"]) == (8, 8, 8, 8, 0, 8)
    assert (s["inj_days"], s["inj_365"]) == (3, 1)
    f = pr.features("a", d0 + timedelta(days=12), "Hard", tk)
    assert f["form10"] > 0.3 and f["tourn_form"] > 0.3 and f["injury"] > 0.9
    assert f["clutch_tb"] > 0 and f["clutch_dec"] > 0                        # gagne plus ses tie-breaks que ses sets
    assert pr.features("a", d0 + timedelta(days=12), "Clay", tk)["surf_form"] == 0          # pas de match sur terre
    # 60 jours sans jouer : le prochain match est un retour de pause, puis la rouille s'estompe
    back = d0 + timedelta(days=80)
    assert pr.stats("a", back, "Hard", tk)["since_break"] == 0
    for k in range(3):
        pr.record(_m(back + timedelta(days=k), "a", f"y{k}", [(6, 4), (6, 4)]), 0.5, tk)
    s = pr.stats("a", back + timedelta(days=4), "Hard", tk)
    assert (s["since_break"], s["break_days"]) == (3, 73)
    assert pr.stats("nouveau", back, "Hard", tk)["since_break"] == 10


@pytest.fixture(scope="module")
def live():
    return make_live()


def _snaps(now, books):
    return pd.DataFrame([dict(fetched_at=now, event_id="e1", sport_key="tennis_atp_x_open", commence_time=now + timedelta(hours=4),
                              player_1="Ana Joueur3", player_2="Ana Joueur7", book=b, book_update=now, odds_1=o1, odds_2=o2)
                         for b, o1, o2 in books])


def test_scan_my_odds_verdicts_and_factors(live, tmp_path):
    from tennisvalue.scan import scan

    s = Settings(db_path=tmp_path / "t.duckdb")
    now = datetime(2026, 10, 28, 10)
    # Pinnacle : J1 ~48 % ; Winamax paie J1 bien plus cher et surestime J2 de ~9 points.
    res = scan(s, snaps=_snaps(now, [("pinnacle", 2.00, 1.87), ("winamax_fr", 2.40, 1.55)]), now=now, live=live).iloc[0]
    assert 0 < res["p_model"] < 1 and res["ma_cote_1"] == pytest.approx(1 / res["p_model"])
    assert res["cote_juste_1"] == pytest.approx(1 / res["p_sharp"])
    assert (res["verdict_1"], res["verdict_2"]) == ("haute", "basse")
    assert res["value_1"] == pytest.approx(res["p_sharp"] * 2.40 - 1) and res["ecart_fr_2"] > 8
    assert (res["statut"], res["pari"]) == ("VALUE", "Ana Joueur3") and res["mise"] > 0
    assert set(res["facteurs"]) == set(GROUPS) and res["tournoi_connu"]
    assert res["profil_1"]["n10"] == 10 and len(res["profil_1"]["form10"]) == 10 and res["h2h"] is not None
    # Cotes françaises alignées sur Pinnacle : aucun verdict
    res = scan(s, snaps=_snaps(now, [("pinnacle", 2.00, 1.87), ("winamax_fr", 1.95, 1.80)]), now=now, live=live).iloc[0]
    assert (res["verdict_1"], res["verdict_2"], res["statut"]) == (None, None, "pas de value")


def test_groups_cover_every_feature():
    cols = [c for _, cs in GROUPS.values() for c in cs]
    assert sorted(cols) == sorted(FEATURES)


def test_outdated_model_is_refused(tmp_path, live):
    import pickle

    from tennisvalue.scan import OutdatedModel, load_live

    s = Settings(models_dir=tmp_path)
    with open(tmp_path / "live_model.pkl", "wb") as fh:
        pickle.dump({**live, "feature_version": FEATURE_VERSION - 1}, fh)
    with pytest.raises(OutdatedModel):
        load_live(s)
