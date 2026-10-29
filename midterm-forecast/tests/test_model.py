"""Tests for the forecast math. Run with:  python -m pytest"""
from datetime import date, timedelta

import pytest

from app.model import DEFAULT_PARAMS, Poll, forecast_race, normal_cdf, poll_weight, simulate_chamber

ELECTION = date(2026, 11, 3)
TODAY = date(2026, 10, 29)


def poll(margin, days_ago=1, pollster="P", n=600, pop="lv", grade=2.0):
    return Poll(pollster, TODAY - timedelta(days=days_ago), margin, n, pop, grade)


def test_normal_cdf_basics():
    assert normal_cdf(0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=0.001)


def test_newer_polls_count_more():
    assert poll_weight(poll(0, days_ago=1), TODAY) > poll_weight(poll(0, days_ago=30), TODAY)


def test_weight_halves_after_half_life():
    fresh = poll_weight(poll(0, days_ago=0), TODAY)
    old = poll_weight(poll(0, days_ago=DEFAULT_PARAMS["half_life_days"]), TODAY)
    assert old == pytest.approx(fresh / 2)


def test_likely_voter_and_better_rated_polls_count_more():
    assert poll_weight(poll(0, pop="lv"), TODAY) > poll_weight(poll(0, pop="a"), TODAY)
    assert poll_weight(poll(0, grade=3.0), TODAY) > poll_weight(poll(0, grade=0.5), TODAY)


def test_huge_samples_are_capped():
    assert poll_weight(poll(0, n=100_000), TODAY) == poll_weight(poll(0, n=50_000), TODAY)


def test_tied_race_is_a_coin_flip():
    fc = forecast_race([poll(3, pollster="A"), poll(-3, pollster="B")], TODAY, ELECTION)
    assert fc.margin == pytest.approx(0)
    assert fc.prob_a == pytest.approx(0.5)


def test_bigger_lead_means_higher_win_chance():
    small = forecast_race([poll(2)], TODAY, ELECTION).prob_a
    big = forecast_race([poll(10)], TODAY, ELECTION).prob_a
    assert 0.5 < small < big < 1


def test_more_polls_means_more_confidence():
    one = forecast_race([poll(5)], TODAY, ELECTION)
    many = forecast_race([poll(5, pollster=f"P{i}") for i in range(10)], TODAY, ELECTION)
    assert many.sigma < one.sigma
    assert many.prob_a > one.prob_a


def test_one_pollster_cannot_dominate():
    # Pollster X releases 4 polls at +10; pollster Y one poll at -10.
    polls = [poll(10, pollster="X") for _ in range(4)] + [poll(-10, pollster="Y")]
    fc = forecast_race(polls, TODAY, ELECTION)
    # Equal-weight average would be +6; the repeat-pollster rule pulls it toward 0.
    assert 0 < fc.margin < 6


def test_weight_shares_add_up_to_one():
    fc = forecast_race([poll(1, pollster="A"), poll(4, pollster="B", days_ago=9)], TODAY, ELECTION)
    assert sum(share for _, share in fc.weights) == pytest.approx(1)


def test_no_polls_is_an_error():
    with pytest.raises(ValueError):
        forecast_race([], TODAY, ELECTION)


def test_simulation_with_only_safe_seats():
    races = [{"holder": "DEM"}] * 3
    out = simulate_chamber(races, {"DEM": 48, "REP": 49}, "REP", n_sims=200)
    assert out["prob_dem_control"] == 1.0          # 51 - 49


def test_simulation_tie_goes_to_vice_president():
    out = simulate_chamber([], {"DEM": 50, "REP": 50}, "DEM", n_sims=100)
    assert out["prob_dem_control"] == 1.0


def test_simulation_toss_up_is_near_even():
    races = [{"margin": 0, "sigma": 5, "party_a": "DEM", "party_b": "REP"}]
    out = simulate_chamber(races, {"DEM": 50, "REP": 49}, "REP", n_sims=4000)
    assert out["prob_dem_control"] == pytest.approx(0.5, abs=0.04)
