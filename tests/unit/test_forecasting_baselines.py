"""
tests/unit/test_forecasting_baselines.py

Unit tests for models/forecasting/baselines.py.
"""

import pytest

from models.forecasting.baselines import (
    historical_average_baseline,
    naive_baseline,
    seasonal_naive_baseline,
)


def test_naive_baseline_returns_last_value():
    assert naive_baseline([10, 20, 30]) == 30.0


def test_naive_baseline_single_value():
    assert naive_baseline([42]) == 42.0


def test_naive_baseline_empty_raises():
    with pytest.raises(ValueError):
        naive_baseline([])


def test_historical_average_baseline_hand_computed():
    assert historical_average_baseline([10, 20, 30]) == pytest.approx(20.0)


def test_historical_average_baseline_single_value():
    assert historical_average_baseline([7]) == 7.0


def test_historical_average_baseline_empty_raises():
    with pytest.raises(ValueError):
        historical_average_baseline([])


def test_baselines_differ_for_a_trending_series():
    """On a trending series, naive (last value) and historical average
    (mean of all prior values) should diverge -- this is exactly why
    both are reported separately rather than picking one."""
    series = [10, 20, 30, 40, 50]
    assert naive_baseline(series) != historical_average_baseline(series)


# --- P1.16: seasonal_naive_baseline ---

def test_seasonal_naive_baseline_returns_prior_season_value():
    # period_ordinal 0..3 = 2 academic years (S1, S2, S1, S2). Forecasting
    # period 4 (next S1) should return period 2's value (last S1), not
    # period 3's (last observed, which is what naive_baseline would do).
    ordinals = [0, 1, 2, 3]
    values = [100.0, 110.0, 120.0, 130.0]
    assert seasonal_naive_baseline(ordinals, values, target_period_ordinal=4) == 120.0


def test_seasonal_naive_baseline_differs_from_naive_on_a_seasonal_series():
    ordinals = [0, 1, 2, 3]
    values = [100.0, 200.0, 105.0, 205.0]  # S1 ~100-105, S2 ~200-205
    seasonal_pred = seasonal_naive_baseline(ordinals, values, target_period_ordinal=4)
    naive_pred = naive_baseline(values)
    assert seasonal_pred == 105.0  # last S1 (period 2), not last observed (period 3)
    assert naive_pred == 205.0
    assert seasonal_pred != naive_pred


def test_seasonal_naive_baseline_missing_prior_season_raises():
    # Only 1 period of history -- target 2 requires period 0, not present
    # for season_length=2 when history starts at period 1.
    with pytest.raises(ValueError):
        seasonal_naive_baseline([1], [50.0], target_period_ordinal=2)


def test_seasonal_naive_baseline_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        seasonal_naive_baseline([0, 1, 2], [10.0, 20.0], target_period_ordinal=3)


def test_seasonal_naive_baseline_respects_custom_season_length():
    ordinals = [0, 1, 2, 3, 4]
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    # season_length=1 degenerates to "last observed value" == naive_baseline
    assert seasonal_naive_baseline(ordinals, values, target_period_ordinal=5, season_length=1) == 50.0