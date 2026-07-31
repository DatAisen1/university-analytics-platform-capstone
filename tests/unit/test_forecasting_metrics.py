"""
tests/unit/test_forecasting_metrics.py

Unit tests for models/forecasting/metrics.py against hand-computed known
values -- Day 20's testing checklist item, made concrete: these are not
"does the function run," they're "does MAE([10,20],[12,18]) actually
equal 2.0," worked out by hand first.
"""

import pytest

from models.forecasting.metrics import mae, mape, r_squared, rmse


def test_mae_hand_computed():
    assert mae([10, 20], [12, 18]) == pytest.approx(2.0)


def test_mae_perfect_prediction_is_zero():
    assert mae([5, 10, 15], [5, 10, 15]) == 0.0


def test_mae_is_symmetric_to_over_and_under_prediction():
    over = mae([10], [15])
    under = mae([10], [5])
    assert over == under == 5.0


def test_rmse_hand_computed():
    import math
    expected = math.sqrt(((10 - 12) ** 2 + (20 - 15) ** 2) / 2)
    assert rmse([10, 20], [12, 15]) == pytest.approx(expected)


def test_rmse_penalizes_large_errors_more_than_mae():
    y_true = [10, 10, 10, 10]
    y_pred = [10, 10, 10, 50]
    assert rmse(y_true, y_pred) > mae(y_true, y_pred)


def test_rmse_equals_mae_when_all_errors_are_identical():
    y_true = [10, 10]
    y_pred = [15, 15]
    assert rmse(y_true, y_pred) == pytest.approx(mae(y_true, y_pred))


def test_mape_hand_computed():
    assert mape([100, 200], [110, 180]) == pytest.approx(10.0)


def test_mape_raises_when_every_actual_is_zero():
    with pytest.raises(ValueError, match="undefined"):
        mape([0, 0], [1, 2])


def test_mape_excludes_zero_actuals_but_keeps_others():
    result = mape([0, 100], [5, 110])
    assert result == pytest.approx(10.0)


def test_r_squared_perfect_fit_is_one():
    assert r_squared([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_r_squared_predicting_the_mean_is_zero():
    y_true = [1, 2, 3, 4, 5]
    mean_pred = [3, 3, 3, 3, 3]
    assert r_squared(y_true, mean_pred) == pytest.approx(0.0)


def test_r_squared_can_be_negative_for_a_bad_model():
    y_true = [1, 2, 3, 4, 5]
    bad_pred = [10, 10, 10, 10, 10]
    assert r_squared(y_true, bad_pred) < 0


def test_r_squared_constant_actual_series_perfect_match():
    assert r_squared([5, 5, 5], [5, 5, 5]) == 1.0


def test_r_squared_constant_actual_series_imperfect_match():
    assert r_squared([5, 5, 5], [6, 5, 4]) == 0.0
