"""
tests/unit/test_forecasting_baselines.py

Unit tests for models/forecasting/baselines.py.
"""

import pytest

from models.forecasting.baselines import historical_average_baseline, naive_baseline


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
