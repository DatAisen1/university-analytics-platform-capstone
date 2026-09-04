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

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy import stats

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
    assert result.detail == "poisson_glm(extrapolation-capped)"  # capped at 4+2*1=6, from an uncapped 6.41
    assert result.yhat > 5.0  # observed real value: 6.0 (capped from an uncapped 6.41 -- see the extrapolation guardrail in count_model.py)
    assert result.yhat_lower < result.yhat < result.yhat_upper


def test_step_change_series_extrapolation_is_capped_not_explosive():
    """Regression test for a real, severe bug found running this against
    live data (P0 gate follow-up, CICT-BSIT-DB's actual
    graduation_count series). A program's first graduating cohort
    produces exactly this shape: many zero periods, then a sudden jump.
    Before the extrapolation guardrail was added, a log-link GLM
    extrapolating this ONE period past training predicted yhat=460 --
    a ~14x blowup over the training max of 33, and yhat_upper=1079 --
    genuinely unusable numbers that were silently corrupting the real
    evaluation_report.md this project generates. The fix must land
    strictly below the training max plus twice the largest observed
    single-period jump (33 + 2*21 = 75), not just "somewhat lower than
    460" -- a partial fix that still overshoots by 5x would pass a
    vague assertion and still be wrong."""
    result = fit_and_predict_count_model(
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 21.0, 21.0, 33.0],
        target_period_ordinal=10,
    )
    assert result.yhat <= 75.0
    assert result.yhat_upper <= 130.0  # generous slack above yhat, still nowhere near the real 1079
    assert "extrapolation-capped" in result.detail
    # The underlying algorithm choice (NB, given real overdispersion here)
    # must still be visible in detail, not overwritten by the cap note.
    assert result.detail.startswith("negative_binomial_glm")


# --- Defensive hardening: non-finite output (P2, forecasting-layer review) ---

def test_non_finite_mu_hat_falls_back_to_sample_mean():
    """Regression test for the guard the extrapolation cap structurally
    cannot provide: `NaN > extrapolation_cap` is always False (unlike
    `inf > extrapolation_cap`, which the cap already catches), so a NaN
    prediction would otherwise slip past the cap unmodified. Forces the
    fitted GLM's own .predict() to return NaN directly -- the most
    faithful way to reproduce "the fit converged but the resulting
    prediction is unusable" without needing to actually engineer a
    numerically pathological design matrix -- and asserts the guard
    replaces it with the plain sample mean, recording this in `detail`."""
    y = [1.0, 2.0, 3.0, 4.0]
    x = [0, 1, 2, 3]
    real_fit = sm.GLM(y, sm.add_constant(np.array(x, dtype=float)), family=sm.families.Poisson()).fit()

    with patch.object(sm.GLM, "fit", return_value=real_fit), \
         patch.object(real_fit, "predict", return_value=np.array([float("nan")])):
        result = fit_and_predict_count_model(x, y, target_period_ordinal=4)

    assert result.yhat == pytest.approx(2.5, abs=1e-6)  # mean([1,2,3,4])
    assert "non-finite-guarded" in result.detail


def test_non_finite_interval_bounds_collapse_to_degenerate_zero_width():
    """Companion guard to the mu_hat one above, for the interval side:
    if the quantile computation (NB's r/p reparameterization, or a
    Poisson ppf call) ever returns a non-finite bound, this must not
    propagate a NaN/inf interval into a deployed forecast row -- it
    should collapse to a zero-width interval at the (already-validated
    finite) point forecast, the same 'no principled uncertainty
    available' signal baselines.BaselineModel already uses deliberately
    elsewhere in this codebase, not a fabricated band."""
    y = [1.0, 2.0, 3.0, 4.0]
    x = [0, 1, 2, 3]

    with patch.object(stats.poisson, "ppf", return_value=float("nan")):
        result = fit_and_predict_count_model(x, y, target_period_ordinal=4)

    assert result.yhat_lower == result.yhat_upper == result.yhat
    assert "interval-guarded" in result.detail


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
    assert out["yhat"].iloc[0] == pytest.approx(6.0, abs=0.01)  # observed real value (capped from an uncapped 6.41)


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