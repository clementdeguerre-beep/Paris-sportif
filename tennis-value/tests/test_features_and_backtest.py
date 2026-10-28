import duckdb
import numpy as np
import pandas as pd
import pytest

from conftest import make_matches
from tennisvalue import db
from tennisvalue.backtest import run as bt
from tennisvalue.ingest.oddsapi import match_player, name_candidates, parse_events, tournament_context
from tennisvalue.models import ensemble
from tennisvalue.models.features import FEATURES, build_features
from tennisvalue.tracking.bets import add_bet, bankroll_report, settle_bet

PRE_MATCH_COLS = ["p_elo", "p_elo_blend", "p_pts", "pts_delta", "games_14d_1", "games_14d_2",
                  "days_since_1", "days_since_2", "h2h_1", "h2h_2", "n_1", "n_2"]


def test_no_leakage(matches):
    """Changer le résultat du match k ne doit modifier aucune variable des matchs 0..k."""
    base, _ = build_features(matches)
    k = 400
    m2 = matches.copy()
    # on inverse le vainqueur du match k et de tous les suivants
    swap = m2.index >= k
    m2.loc[swap, ["winner", "loser"]] = m2.loc[swap, ["loser", "winner"]].to_numpy()
    m2.loc[swap, ["winner_key", "loser_key"]] = m2.loc[swap, ["loser_key", "winner_key"]].to_numpy()
    m2.loc[swap, ["w_games", "l_games"]] = m2.loc[swap, ["l_games", "w_games"]].to_numpy()
    alt, _ = build_features(m2)
    b = base.set_index("match_id")
    a = alt.set_index("match_id")
    ids_before = matches["match_id"].iloc[:k]
    # mêmes variables pour tous les matchs antérieurs
    pd.testing.assert_frame_equal(b.loc[ids_before, PRE_MATCH_COLS], a.loc[ids_before, PRE_MATCH_COLS])
    # et pour le match k lui-même : ses variables ne dépendent pas de son résultat
    # (son identifiant change car le vainqueur change : on compare par position)
    row_b = base.iloc[k]
    row_a = alt.iloc[k]
    assert {row_b["p1_key"], row_b["p2_key"]} == {row_a["p1_key"], row_a["p2_key"]}
    p_b = row_b["p_elo"] if row_b["p1_key"] == row_a["p1_key"] else 1 - row_b["p_elo"]
    assert p_b == pytest.approx(row_a["p_elo"])


def test_orientation_is_balanced(matches):
    f, _ = build_features(matches)
    assert 0.4 < f["y"].mean() < 0.6


def test_walk_forward_uses_only_past():
    m = make_matches(n=1800, n_players=40, seed=1)
    f, _ = build_features(m)
    p = ensemble.walk_forward(f, [2019, 2020, 2021, 2022, 2023, 2024], "logit")
    assert p[f.season < 2019].isna().all()          # pas de prédiction sans passé suffisant
    # 1 000 matchs d'entraînement minimum : atteint à partir de 2022 (2017-2021 = 5 x 200)
    assert p[(f.season == 2021)].isna().all()
    assert p[(f.season >= 2022) & f.completed].notna().all()
    assert set(FEATURES) <= set(f.columns)


def test_backtest_selects_value_side():
    df = pd.DataFrame({
        "match_id": ["m1", "m2"], "date": [pd.Timestamp("2024-01-01").date()] * 2, "season": [2024, 2024],
        "tour": ["ATP"] * 2, "tournament": ["X"] * 2, "tier": ["ATP250"] * 2, "surface": ["Hard"] * 2,
        "p1": ["A", "C"], "p2": ["B", "D"], "y": [1, 0], "completed": [True, True],
        "p_model": [0.60, 0.50], "p_sharp": [0.58, 0.50],
        "odds_b365_1": [2.00, 1.90], "odds_b365_2": [1.80, 1.90],
    })
    bets = bt.candidate_bets(df, "b365", "modele", 0.05, bt.BacktestConfig())
    assert list(bets["selection"]) == ["A"]           # EV = 0.60 x 2.00 - 1 = 20 %
    assert bets["ret"].iloc[0] == pytest.approx(1.0)
    sharp = bt.candidate_bets(df, "b365", "sharp", 0.05, bt.BacktestConfig())
    assert list(sharp["selection"]) == ["A"]          # 0.58 x 2.00 - 1 = 16 %


def test_odds_api_parsing_and_names():
    events = [{
        "id": "ev1", "sport_key": "tennis_atp_paris_masters", "commence_time": "2026-10-28T13:00:00Z",
        "home_team": "Carlos Alcaraz", "away_team": "Felix Auger-Aliassime",
        "bookmakers": [{"key": "winamax_fr", "title": "Winamax", "last_update": "2026-10-27T20:00:00Z",
                        "markets": [{"key": "h2h", "last_update": "2026-10-27T20:00:00Z",
                                     "outcomes": [{"name": "Carlos Alcaraz", "price": 1.45},
                                                  {"name": "Felix Auger-Aliassime", "price": 2.75}]}]}],
    }]
    df = parse_events(events)
    assert len(df) == 1 and df.iloc[0]["odds_2"] == 2.75
    assert "alcaraz c" in name_candidates("Carlos Alcaraz")
    assert match_player("Felix Auger-Aliassime", {"auger aliassime f"}) == "auger aliassime f"
    assert match_player("Juan Manuel Cerundolo", {"cerundolo j m"}) == "cerundolo j m"
    keys = {"fernandez l": 8, "fernandez l a": 278, "fernandez m": 50}      # même joueuse écrite de deux façons
    assert match_player("Leylah Fernandez", set(keys), keys) == "fernandez l a"
    assert match_player("Leylah Fernandez", set(keys)) == "fernandez l"
    ctx = tournament_context("tennis_atp_french_open")
    assert ctx["surface"] == "Clay" and ctx["best_of"] == 5
    assert tournament_context("tennis_wta_wimbledon")["best_of"] == 3


def test_bet_tracking(tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    b1 = add_bet(con, match="A - B", selection="A", book="winamax_fr", odds=2.0, stake=10, p_decision=0.55)
    b2 = add_bet(con, match="C - D", selection="D", book="betclic_fr", odds=3.0, stake=5)
    settle_bet(con, b1, "gagne")
    settle_bet(con, b2, "perdu")
    rep = bankroll_report(con, 1000)
    assert rep["benefice"] == pytest.approx(5.0)
    assert rep["bankroll"] == pytest.approx(1005.0)
    assert rep["yield"] == pytest.approx(5 / 15)
