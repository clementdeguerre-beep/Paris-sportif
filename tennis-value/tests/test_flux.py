import pandas as pd
import pytest

from tennisvalue.market.devig import fair_two_way
from tennisvalue.market.flux import best_odds, summary, timeline

SHARP = ("pinnacle", "betfair_ex_eu")
T0 = pd.Timestamp("2026-10-28 08:00")


def _snaps(rows):
    """rows : (heures après T0, event_id, book, cote J1, cote J2)."""
    return pd.DataFrame([dict(fetched_at=T0 + pd.Timedelta(hours=h), event_id=ev, sport_key="tennis_atp_x",
                              commence_time=T0 + pd.Timedelta(hours=12), player_1="A", player_2="B", book=book,
                              book_update=T0, odds_1=o1, odds_2=o2) for h, ev, book, o1, o2 in rows])


def test_public_money_moves_only_french_books():
    s = _snaps([(0, "e", "winamax_fr", 2.00, 1.80), (0, "e", "betclic_fr", 2.00, 1.80), (0, "e", "pinnacle", 2.05, 1.85),
                (3, "e", "winamax_fr", 1.80, 2.00), (3, "e", "betclic_fr", 1.85, 1.95), (3, "e", "pinnacle", 2.05, 1.85)])
    t = timeline(s, SHARP)
    assert list(t.columns) == ["event_id", "fetched_at", "fr", "sharp"] and len(t) == 2
    r = summary(s, SHARP).iloc[0]
    expected = 100 * ((fair_two_way(1.80, 2.00)[0] + fair_two_way(1.85, 1.95)[0]) / 2 - fair_two_way(2.0, 1.8)[0])
    assert r["delta_fr"] == pytest.approx(expected) and r["delta_fr"] > 3
    assert r["delta_sharp"] == pytest.approx(0)
    assert (r["vers"], r["origine"], r["n_releves"]) == (1, "grand public", 2)


def test_informed_money_and_contradiction():
    informed = _snaps([(0, "e", "winamax_fr", 2.0, 1.8), (0, "e", "pinnacle", 2.0, 1.85),
                       (3, "e", "winamax_fr", 2.3, 1.6), (3, "e", "pinnacle", 2.4, 1.6)])
    r = summary(informed, SHARP).iloc[0]
    assert (r["vers"], r["origine"]) == (2, "informé")
    opposite = _snaps([(0, "e", "winamax_fr", 2.0, 1.8), (0, "e", "pinnacle", 2.0, 1.85),
                       (3, "e", "winamax_fr", 1.8, 2.0), (3, "e", "pinnacle", 2.4, 1.6)])
    assert summary(opposite, SHARP).iloc[0]["origine"] == "contradictoire"
    lagging = _snaps([(0, "e", "winamax_fr", 2.0, 1.8), (0, "e", "pinnacle", 2.0, 1.85),
                      (3, "e", "winamax_fr", 2.0, 1.8), (3, "e", "pinnacle", 1.7, 2.2)])     # français en retard
    r = summary(lagging, SHARP).iloc[0]
    assert (r["vers"], r["origine"]) == (1, "informé") and r["flux"] == pytest.approx(r["delta_sharp"])


def test_stable_single_snapshot_and_window():
    one = _snaps([(0, "e", "winamax_fr", 2.0, 1.8)])
    r = summary(one, SHARP).iloc[0]
    assert (r["flux"], r["vers"], r["origine"]) == (0.0, 0, "stable") and pd.isna(r["delta_sharp"])
    s = _snaps([(0, "e", "winamax_fr", 2.6, 1.5), (3, "e", "winamax_fr", 2.0, 1.8), (6, "e", "winamax_fr", 2.0, 1.8)])
    assert summary(s, SHARP).iloc[0]["vers"] == 1                                       # depuis l'ouverture
    r = summary(s, SHARP, since=T0 + pd.Timedelta(hours=4)).iloc[0]                       # depuis 3 h : stable
    assert (r["vers"], r["n_releves"]) == (0, 2)


def test_best_odds_open_and_now():
    s = _snaps([(0, "e", "winamax_fr", 2.0, 1.8), (0, "e", "unibet_fr", 1.9, 1.9), (0, "e", "pinnacle", 2.5, 1.6),
                (3, "e", "winamax_fr", 1.7, 2.1), (3, "e", "unibet_fr", 1.75, 2.05)])
    r = best_odds(s).iloc[0]
    assert (r["cote_1"], r["book_1"], r["cote_2"], r["book_2"]) == (1.75, "unibet_fr", 2.1, "winamax_fr")
    assert (r["ouv_1"], r["ouv_2"]) == (2.0, 1.9)                                        # Pinnacle exclu
