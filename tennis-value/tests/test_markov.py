import pytest

from tennisvalue.models.markov import (
    final_set_rule, hold_prob, match_distribution, match_prob, set_distribution, tiebreak_prob,
)


def test_hold_known_values():
    assert hold_prob(0.5) == pytest.approx(0.5)
    # calcul à la main : 0.6^4 x 4.2 + 20 x 0.6^3 x 0.4^3 x 0.36 / 0.52 = 0.73573
    assert hold_prob(0.6) == pytest.approx(0.73573, abs=1e-5)


def test_symmetry():
    assert tiebreak_prob(0.64, 0.64, 7) == pytest.approx(0.5)
    assert tiebreak_prob(0.64, 0.64, 10) == pytest.approx(0.5)
    for bo in (3, 5):
        for rule in ("tb7", "tb10", "adv"):
            assert match_prob(0.62, 0.62, bo, rule) == pytest.approx(0.5)
    assert match_prob(0.66, 0.60) == pytest.approx(1 - match_prob(0.60, 0.66))


def test_monotonic_and_best_of_five_helps_favourite():
    p3 = match_prob(0.66, 0.62, 3)
    p5 = match_prob(0.66, 0.62, 5)
    assert 0.5 < p3 < p5 < 1
    assert match_prob(0.68, 0.62) > p3


def test_distributions_sum_to_one_and_match_marginal():
    for first in (True, False):
        for rule in ("tb7", "tb10", "adv"):
            s = sum(p for _, p in set_distribution(0.65, 0.61, first, rule))
            assert s == pytest.approx(1.0)
    dist = match_distribution(0.65, 0.61, 5, "tb10")
    assert sum(dist.values()) == pytest.approx(1.0)
    p_win = sum(v for (sa, sb, _, _), v in dist.items() if sa > sb)
    assert p_win == pytest.approx(match_prob(0.65, 0.61, 5, "tb10"))


def test_final_set_rules():
    assert final_set_rule("ATP250", 2018, "Doha") == "tb7"
    assert final_set_rule("Grand Slam", 2023, "Wimbledon") == "tb10"
    assert final_set_rule("Grand Slam", 2018, "Australian Open") == "adv"
    assert final_set_rule("Grand Slam", 2019, "Australian Open") == "tb10"
    assert final_set_rule("Grand Slam", 2021, "French Open") == "adv"
    assert final_set_rule("Grand Slam", 2020, "US Open") == "tb7"
