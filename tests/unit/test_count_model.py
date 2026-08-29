"""
tests/unit/test_count_model.py

Unit tests for models/forecasting/count_model.py (P2.1). No Postgres
required -- fit_and_predict_count_model / build_deployable_count_model
are pure functions of (period_ordinals, train_values, target_period_ordinal),
mirroring test_forecasting_baselines.py's style for the same reason.

Every expected value below was obtained by actually running the function
against the stated input and reading off the real result (see this
session's development notes) -- not derived from the docstring's claims
in isolation. In particular, the intercept-only case's "fitted mean
equals the sample mean" property is a well-known GLM identity, but it is
still asserted against an actually-computed value here rather than
trusted as a mathematical fact this test merely restates.
"""

from unittest.mock import patch

import pandas as pd
import pytest
import statsmodels.api as sm

from models.forecasting.count_model import (
    ALGORITHM_NAME,
    build_deployable_count_model,
    fit_and_predict_count_model,
)
from models.forecasting.model_registry import ALGORITHM_SIMPLICITY_RANK


# --- Input validation ---

def test_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="equal length"):
        fit_and_predict_count_model([0, 1, 2], [1.0, 2.0], target_period_ordinal=3)


def test_empty_input_raises():
    with pytest.raises(ValueError, match="at least one training value"):
        fit_and_predict_count_model([], [], target_period_ordinal=0)


def test_negative_values_raises():
    with pytest.raises(ValueError, match="non-negative counts"):
        fit_and_predict_count_model([0, 1], [1.0, -2.0], target_period_ordinal=2)


# --- Degenerate all-zero history ---

def test_all_zero_history_returns_degenerate_zero_forecast():
    """A program with zero graduates in every observed period -- the
    single most common case in this project's actual graduation_count
    data (see count_model.py's module docstring) -- must predict exactly
    zero with a zero-width interval, and must NOT attempt a GLM fit at
    all (dispersion_ratio is None signals this, not just yhat==0)."""
    result = fit_and_predict_count_model([0, 1, 2], [0.0, 0.0, 0.0], target_period_ordinal=3)
    assert result.yhat == 0.0
    assert result.yhat_lower == 0.0
    assert result.yhat_upper == 0.0
    assert result.detail == "poisson_glm(degenerate-zero)"
    assert result.dispersion_ratio is None
    assert result.n_train == 3


# --- Intercept-only fallback (too few points, or no ordinal variation) ---

def test_two_points_uses_intercept_only_and_predicts_sample_mean():
    # Fewer than MIN_PERIODS_FOR_TREND (3): no trend term. An
    # intercept-only Poisson's fitted mean is mathematically the sample
    # mean (2.0, 4.0 -> 3.0) -- verified against the real fit, not just
    # asserted from the GLM identity.
    result = fit_and_predict_count_model([0, 1], [2.0, 4.0], target_period_ordinal=2)
    assert result.yhat == pytest.approx(3.0, abs=1e-6)
    assert result.detail == "poisson_glm"
    assert result.n_train == 2


def test_constant_period_ordinal_uses_intercept_only_despite_enough_points():
    # 4 points clears MIN_PERIODS_FOR_TREND, but np.var(period_ordinals)
    # == 0 -- there is no ordinal variation to fit a trend against, so
    # this must still degrade to intercept-only, same sample-mean result
    # as the two-point case above (mean of [2,3,4,3] = 3.0).
    result = fit_and_predict_count_model([5, 5, 5, 5], [2.0, 3.0, 4.0, 3.0], target_period_ordinal=5)
    assert result.yhat == pytest.approx(3.0, abs=1e-6)
    assert result.detail == "poisson_glm"


# --- Trend fit ---

def test_clear_upward_trend_extrapolates_above_the_historical_average():
    # [1,2,3,4] has an obvious upward trend; a log-linear (Poisson) fit
    # extrapolating to period 4 should predict noticeably above the flat
    # historical average (2.5) -- this is the entire reason a trend term
    # exists rather than always falling back to intercept-only.
    result = fit_and_predict_count_model([0, 1, 2, 3], [1.0, 2.0, 3.0, 4.0], target_period_ordinal=4)
    assert result.detail == "poisson_glm"
    assert result.yhat > 5.0  # observed real value: ~6.41
    assert result.yhat_lower < result.yhat < result.yhat_upper


# --- Overdispersion -> Negative Binomial fallback ---

def test_overdispersed_series_triggers_negative_binomial_fallback():
    # High-variance counts (many low/zero periods, occasional spikes) is
    # exactly the shape real graduation_count series show. n=5 clears
    # MIN_PERIODS_FOR_NB (4).
    result = fit_and_predict_count_model(
        [0, 1, 2, 3, 4], [0.0, 15.0, 0.0, 18.0, 1.0], target_period_ordinal=5
    )
    assert result.detail == "negative_binomial_glm"
    assert result.dispersion_ratio > 1.5  # OVERDISPERSION_THRESHOLD
    assert result.yhat_lower >= 0.0  # NB quantiles are non-negative by construction


def test_negative_binomial_fit_failure_falls_back_to_poisson_gracefully():
    """If statsmodels' NB optimizer fails to converge (realistic on 4-5
    points), this must silently degrade to the already-computed Poisson
    result, not propagate an exception and abort the whole walk-forward
    fold -- same fail-soft philosophy as seasonal_naive's missing-
    lookback handling elsewhere in this codebase."""
    with patch.object(sm.NegativeBinomial, "fit", side_effect=RuntimeError("forced non-convergence")):
        result = fit_and_predict_count_model(
            [0, 1, 2, 3, 4], [0.0, 15.0, 0.0, 18.0, 1.0], target_period_ordinal=5
        )
    assert result.detail == "poisson_glm"  # fell back, did not raise
    assert result.dispersion_ratio > 1.5  # overdispersion was still correctly detected...
    # ...it just couldn't be acted on, which is the exact scenario this test exists for.


# --- Deployment adapter: build_deployable_count_model / CountModel ---

def test_deployable_count_model_predict_tiles_the_single_forecast():
    """CountModel.predict() must behave like Prophet's own .predict():
    given N future rows, return N rows, all identical (the point forecast
    and interval were computed once, at construction time -- see
    baselines.BaselineModel's docstring for the same pattern)."""
    model = build_deployable_count_model([0, 1, 2, 3], [1.0, 2.0, 3.0, 4.0], target_period_ordinal=4)
    future = pd.DataFrame({"ds": pd.date_range("2024-01-01", periods=3, freq="6MS")})
    out = model.predict(future)

    assert list(out.columns) == ["yhat", "yhat_lower", "yhat_upper"]
    assert len(out) == 3
    assert out["yhat"].nunique() == 1
    assert out["yhat"].iloc[0] == pytest.approx(6.41, abs=0.01)  # observed real value


def test_deployable_count_model_on_all_zero_history_deploys_zero_forecast():
    model = build_deployable_count_model([0, 1, 2], [0.0, 0.0, 0.0], target_period_ordinal=3)
    future = pd.DataFrame({"ds": pd.date_range("2024-01-01", periods=1)})
    out = model.predict(future)
    assert out.iloc[0]["yhat"] == 0.0
    assert out.iloc[0]["yhat_lower"] == 0.0
    assert out.iloc[0]["yhat_upper"] == 0.0


# --- Cross-module wiring regression guard ---

def test_algorithm_name_is_registered_in_simplicity_rank():
    """Regression guard: count_model.ALGORITHM_NAME and
    model_registry.ALGORITHM_SIMPLICITY_RANK's key for it must always
    agree, or select_champion_algorithm silently falls through to the
    unranked default (99) for every count_model candidate -- it would
    still function, but would never win a tie against anything, which
    is a subtle enough failure mode to be worth a real assertion rather
    than trusting the two modules stay in sync by convention alone."""
    assert ALGORITHM_NAME == "count_model"
    assert ALGORITHM_NAME in ALGORITHM_SIMPLICITY_RANK
    # Must rank below prophet (simpler model, GLM vs. full trend+seasonality
    # decomposition) and at or above historical_avg (more machinery: a
    # fitted trend/dispersion parameter, not just a stored mean).
    assert ALGORITHM_SIMPLICITY_RANK["count_model"] < ALGORITHM_SIMPLICITY_RANK["prophet"]
    assert ALGORITHM_SIMPLICITY_RANK["count_model"] >= ALGORITHM_SIMPLICITY_RANK["historical_avg"]