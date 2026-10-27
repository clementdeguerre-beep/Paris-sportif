import math

import pandas as pd
import pytest

from tennisvalue.market.devig import METHODS, fair_probs, margin
from tennisvalue.market.movement import clv, open_close, stale_lines, steam_moves
from tennisvalue.market.staking import (
    StakingRules, expected_value, kelly_fraction, recommended_stake, scale_to_exposure,
)


def test_margin():
    assert margin([1.90, 1.90]) == pytest.approx(2 / 1.9 - 1)


@pytest.mark.parametrize("method", list(METHODS))
def test_devig_sums_to_one(method):
    p = fair_probs([1.30, 3.60], method)
    assert sum(p) == pytest.approx(1.0)
    assert p[0] > p[1]


def test_fair_odds_unchanged():
    for method in METHODS:
        assert fair_probs([2.0, 2.0], method) == pytest.approx([0.5, 0.5])


def test_power_loads_margin_on_longshot():
    mult = fair_probs([1.30, 3.60], "multiplicative")
    powr = fair_probs([1.30, 3.60], "power")
    assert powr[0] > mult[0]  # le favori récupère une proba plus élevée


def test_invalid_odds_give_nan():
    assert all(math.isnan(x) for x in fair_probs([1.0, 2.0]))
    assert all(math.isnan(x) for x in fair_probs([1.05, 1.05]))  # marge absurde (> 30 %)


def test_kelly():
    assert expected_value(0.55, 2.0) == pytest.approx(0.10)
    assert kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    assert kelly_fraction(0.40, 2.0) == 0.0
    rules = StakingRules(kelly_multiplier=0.25, max_stake_pct=0.02)
    assert recommended_stake(0.55, 2.0, 1000, rules) == pytest.approx(20.0)  # 2.5 % plafonné à 2 %
    assert recommended_stake(0.52, 2.0, 1000, rules) == pytest.approx(10.0)  # 4 % x 1/4 = 1 %
    assert sum(scale_to_exposure([60, 60], 1000, StakingRules(max_exposure_pct=0.10))) == pytest.approx(100)


def _snaps():
    t0 = pd.Timestamp("2026-10-27 10:00")
    start = pd.Timestamp("2026-10-27 18:00")
    rows = []
    for k, (o1, o2) in enumerate([(2.00, 1.90), (1.95, 1.95), (1.70, 2.25)]):
        rows.append(dict(fetched_at=t0 + pd.Timedelta(minutes=20 * k), event_id="e1", sport_key="tennis_atp_x",
                         commence_time=start, player_1="A", player_2="B", book="pinnacle",
                         book_update=t0, odds_1=o1, odds_2=o2))
    rows.append(dict(fetched_at=t0 + pd.Timedelta(minutes=40), event_id="e1", sport_key="tennis_atp_x",
                     commence_time=start, player_1="A", player_2="B", book="winamax_fr",
                     book_update=t0, odds_1=2.05, odds_2=1.72))
    return pd.DataFrame(rows)


def test_open_close_and_steam():
    s = _snaps()
    oc = open_close(s).set_index("book")
    assert oc.loc["pinnacle", "move_pp_1"] > 5
    assert oc.loc["pinnacle", "direction"] == "joueur 1 raccourcit"
    steam = steam_moves(s, ("pinnacle",), move_pp=3, window_min=60)
    assert len(steam) == 1 and steam.iloc[0]["move_pp_1"] > 3


def test_stale_line_detected():
    s = _snaps()
    latest = s.sort_values("fetched_at").groupby(["event_id", "book"]).last().reset_index()
    stale = stale_lines(latest, ("pinnacle",), gap_pp=2.5)
    assert len(stale) == 1
    assert stale.iloc[0]["selection"] == "A" and stale.iloc[0]["ev_sharp"] > 0


def test_clv():
    assert clv(2.10, 0.50) == pytest.approx(0.05)
