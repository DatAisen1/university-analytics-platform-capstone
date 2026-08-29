"""
models/forecasting/count_model.py

P2.1 (docs/21_Option_B_Punchlist.md follow-up): a count-distribution
candidate algorithm -- Poisson GLM, with an automatic Negative Binomial
fallback under detected overdispersion -- for `graduation_count`.

Why this exists, and why only now
----------------------------------
This was NOT built speculatively. `forecasting/artifacts/evaluation_report.md`
(a real walk-forward run, P1.6's own diagnostic) already shows the
measurement that justifies it:

    `graduation_count`: 0 of 37 (0%) -- Prophet beats its baseline on
    NOT ONE graduation_count series.

Two genuinely different things are hiding inside that 0%, and only one
of them is a modeling problem:

1. Many series have an all-zero actual in every walk-forward test fold
   (a new or small program that simply hasn't produced a graduating
   cohort yet in the 6-semester observed window). Prophet, every
   baseline, AND this count model all correctly predict ~0 there --
   MAE is 0 for everyone, "no champion" only because nothing beats a
   tie. No algorithm fixes a program that hasn't graduated anyone yet;
   this is a data-maturity limitation (see docs/10_Forecasting.md SS2),
   not a model-choice one, and this module does not attempt to solve it.
2. On the NONZERO subset -- the actually forecastable series -- Prophet
   is not just losing, it is losing badly: e.g. COA-CERT-DRAFT (Prophet
   MAE 7.12 vs. best baseline 2.67, R^2 -17.5), COED-CERT-PTE (Prophet
   MAE 9.59 vs. baseline 3.15, R^2 -8.2). Negative R^2 this large means
   Prophet's fitted curve is doing considerably worse than just
   guessing the training mean. That is exactly the failure signature of
   fitting a Gaussian-noise, continuous-valued trend+seasonality model
   to a small, non-negative, integer-valued, right-skewed count series
   -- the distributional mismatch a Poisson/Negative-Binomial model is
   built to respect instead of ignore.

This module's function is added as ONE MORE candidate in the existing
Option B champion-selection framework (models/forecasting/model_registry
.select_champion_algorithm) -- it competes on walk-forward MAE exactly
like prophet/naive/historical_avg/seasonal_naive already do, and is
gated to `graduation_count` only (see train_prophet.walk_forward_evaluate)
because that is the ONLY metric the measurement above shows a problem
for. `enrollment_count` already has Prophet beating baseline 78% of the
time (SS8 of docs/10_Forecasting.md) -- there is no diagnosed gap there,
so this model is never even fit for that metric. If a future
`graduation_count` measurement stops showing this pattern (e.g. once
more academic periods make the nonzero subset less sparse), the
honest response is to re-run the measurement, not to assume this model
keeps earning its place.

Design, given the data volume this project actually has
----------------------------------------------------------
Walk-forward folds here train on as few as 3 and at most 5 points
(docs/10_Forecasting.md SS5's fold table) -- far too little to fit a
Poisson GLM's dispersion parameter reliably, let alone a Negative
Binomial's. This module is written assuming that reality, not despite
it:

  - **All-zero training history** -> a degenerate zero forecast
    (yhat = 0, zero-width interval), no GLM fit at all. Fitting a
    Poisson MLE to an all-zero sample converges to lambda=0 anyway;
    skipping the fit avoids a redundant optimizer call and a lambda=0
    edge case in the interval-quantile code below.
  - **Fewer than 3 distinct periods, or a constant period_ordinal
    within the training window** -> intercept-only Poisson (no trend
    term). With 1-2 points there is no meaningful trend to estimate;
    an intercept-only Poisson's fitted mean is mathematically the
    sample mean, same point forecast historical_average_baseline
    already produces, but with principled Poisson prediction-interval
    quantiles instead of baselines.py's deliberately degenerate
    zero-width interval (see that module's docstring) -- this
    difference in kind, not in point-forecast MAE, is why an
    intercept-only fit is still worth doing rather than skipped.
  - **3+ distinct periods with variation** -> Poisson GLM with
    period_ordinal as a linear trend term on the log link.
  - **Overdispersion detected** (Pearson chi-square / residual df >
    OVERDISPERSION_THRESHOLD) **and** at least MIN_PERIODS_FOR_NB
    points -> attempt a Negative Binomial refit (which additionally
    estimates a dispersion parameter, alpha). Any fit failure
    (non-convergence, degenerate alpha) falls back to the Poisson
    result rather than propagating -- an unstable extra parameter on
    4-5 data points is exactly the kind of thing that should silently
    degrade to the simpler model, not crash a walk-forward fold, the
    same fail-soft philosophy walk_forward_evaluate already applies to
    seasonal_naive's missing-lookback case.

**Disclosed limitation, matching this project's existing epistemic
honesty standard for small-sample statistics (see metrics.py's r_squared
docstring and docs/10_Forecasting.md SS5):** with only 3-5 points, both
the trend slope and (especially) the Negative Binomial's alpha are
themselves noisy estimates. A walk-forward MAE difference of a
fraction of a graduating student between this model and a baseline is
not a precise measurement of which is "truly" better -- it is a point
estimate, exactly as unstable as everything else this project reports
at this fold count. This model earns champion status the same way
everything else does: by winning select_champion_algorithm() on
measured walk-forward MAE, not by assumption.

Prediction intervals use Poisson/Negative-Binomial quantiles (10th/90th
percentile, an 80% interval -- matching Prophet's default interval_width
so the two are reported on the same footing) rather than a Gaussian
approximation, and are non-negative and integer-valued by construction
-- no `max(0.0, yhat_lower)` clipping is needed here the way
deploy_forecast._forecast_next_period must apply to Prophet's Gaussian
interval, because a Poisson/NB quantile cannot be negative in the
first place.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from pipelines.common.errors import ModelTrainingError

#: Pearson chi-square / residual-degrees-of-freedom ratio above which the
#: Poisson fit is treated as overdispersed and a Negative Binomial refit
#: is attempted. 1.0 is "exactly Poisson-consistent"; some slack above
#: that is expected from sampling noise alone at this sample size, so
#: 1.5 (not 1.0) avoids chasing every fold's noise-level excess variance
#: into an extra, harder-to-estimate parameter.
OVERDISPERSION_THRESHOLD = 1.5

#: Minimum distinct training points before a Negative Binomial refit is
#: even attempted. NB estimates one more parameter (alpha) than Poisson
#: on top of the same intercept(+trend); attempting that on 3 points
#: leaves zero residual degrees of freedom to estimate it from.
MIN_PERIODS_FOR_NB = 4

#: Minimum distinct training points before a trend (period_ordinal) term
#: is included at all -- below this, only an intercept (i.e. a constant
#: rate) is fit. Matches this module's docstring design section above.
MIN_PERIODS_FOR_TREND = 3

#: The single registrable algorithm name this module's fits are reported
#: under in walk-forward results / gold.model_registry.algorithm --
#: which of {poisson_glm, negative_binomial_glm} was actually used for a
#: given fold/deployment is a diagnostic detail (see CountModelFit.detail),
#: not a separate top-level algorithm identity. Keeping ONE name here
#: keeps ALGORITHM_SIMPLICITY_RANK, make_model_version, and
#: deploy_forecast._build_champion_model's dispatch as simple as every
#: other registered algorithm, instead of needing two.
ALGORITHM_NAME = "count_model"


@dataclass(frozen=True)
class CountModelFit:
    """One fit-and-predict-a-single-point result. `detail` records which
    distribution was actually used (diagnostic/testing only -- not part
    of the champion-selection contract, see ALGORITHM_NAME above)."""

    yhat: float
    yhat_lower: float
    yhat_upper: float
    detail: str  # "poisson_glm" | "poisson_glm(degenerate-zero)" | "negative_binomial_glm"
    dispersion_ratio: Optional[float]  # None only for the degenerate-zero case
    n_train: int


def _validate_inputs(period_ordinals: Sequence[int], train_values: Sequence[float]) -> None:
    if len(period_ordinals) != len(train_values):
        raise ValueError(
            "fit_and_predict_count_model requires period_ordinals and "
            f"train_values of equal length (got {len(period_ordinals)} and {len(train_values)})"
        )
    if len(train_values) == 0:
        raise ValueError("fit_and_predict_count_model requires at least one training value")
    if any(v < 0 for v in train_values):
        raise ValueError(
            "fit_and_predict_count_model requires non-negative counts -- "
            "graduation_count cannot be negative"
        )


def fit_and_predict_count_model(
    period_ordinals: Sequence[int],
    train_values: Sequence[float],
    target_period_ordinal: int,
    overdispersion_threshold: float = OVERDISPERSION_THRESHOLD,
    min_periods_for_nb: int = MIN_PERIODS_FOR_NB,
) -> CountModelFit:
    """Fit a Poisson (or, under detected overdispersion, Negative
    Binomial) GLM on `train_values ~ period_ordinal` and predict the
    single point `target_period_ordinal`. See module docstring for the
    full small-sample design rationale.

    Mirrors baselines.py's function shape deliberately (period_ordinals
    + train_values + target_period_ordinal in, a point-forecast-shaped
    result out) so it slots into walk_forward_evaluate the same way
    naive_baseline/historical_average_baseline/seasonal_naive_baseline
    already do -- the one difference is this returns a CountModelFit
    (point + interval + diagnostics), not a bare float, because
    build_deployable_count_model (below) needs the interval too and
    shouldn't have to refit to get it.

    Raises ValueError only for malformed input (see _validate_inputs) --
    never for "not enough data to fit a trend" or "NB didn't converge";
    those are handled by falling back to a simpler fit within this
    function, not by raising, since has_sufficient_history already
    guarantees callers have at least MIN_HISTORY_PERIODS points and a
    thin fold should degrade gracefully, not abort the series.
    """
    _validate_inputs(period_ordinals, train_values)

    x = np.asarray(period_ordinals, dtype=float)
    y = np.asarray(train_values, dtype=float)
    n = len(y)

    if np.allclose(y, 0.0):
        # A Poisson MLE fit to an all-zero sample converges to lambda=0
        # anyway (see module docstring) -- this branch just skips the
        # optimizer call and the lambda=0 quantile edge case below.
        return CountModelFit(
            yhat=0.0, yhat_lower=0.0, yhat_upper=0.0,
            detail="poisson_glm(degenerate-zero)", dispersion_ratio=None, n_train=n,
        )

    import statsmodels.api as sm
    from scipy import stats

    use_trend = n >= MIN_PERIODS_FOR_TREND and np.var(x) > 0
    design = sm.add_constant(x) if use_trend else np.ones((n, 1))

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            poisson_fit = sm.GLM(y, design, family=sm.families.Poisson()).fit()
    except Exception as exc:
        raise ModelTrainingError(
            f"count_model Poisson GLM fit failed: {exc}", stage="Model Training",
            entity="graduation_count", rows_affected=n,
        ) from exc

    mu_train = np.asarray(poisson_fit.mu, dtype=float)
    df_resid = max(n - design.shape[1], 1)
    dispersion_ratio = float(np.sum((y - mu_train) ** 2 / np.maximum(mu_train, 1e-6)) / df_resid)

    fit_result = poisson_fit
    detail = "poisson_glm"
    if dispersion_ratio > overdispersion_threshold and n >= min_periods_for_nb:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                nb_fit = sm.NegativeBinomial(y, design).fit(disp=False)
            alpha = float(nb_fit.params[-1])
            if alpha > 0 and np.isfinite(alpha):
                fit_result = nb_fit
                detail = "negative_binomial_glm"
            # A non-positive or non-finite alpha is a degenerate NB fit
            # (equivalent to, or worse than, Poisson) -- keep the Poisson
            # result rather than deploy a nonsensical dispersion estimate.
        except Exception:
            # Non-convergence on 4-5 points is expected sometimes -- fall
            # back to the already-fitted Poisson result, not a crash.
            pass

    target_design = (
        np.array([[1.0, float(target_period_ordinal)]]) if use_trend else np.array([[1.0]])
    )
    mu_hat = float(fit_result.predict(target_design)[0])
    mu_hat = max(mu_hat, 0.0)

    if detail == "negative_binomial_glm":
        alpha = float(fit_result.params[-1])
        var_hat = mu_hat + alpha * mu_hat ** 2
        # NB parameterized as (r, p) for scipy.stats.nbinom: mean = r(1-p)/p.
        r = mu_hat ** 2 / (var_hat - mu_hat)
        p = r / (r + mu_hat)
        yhat_lower = float(stats.nbinom.ppf(0.1, r, p))
        yhat_upper = float(stats.nbinom.ppf(0.9, r, p))
    else:
        yhat_lower = float(stats.poisson.ppf(0.1, mu_hat)) if mu_hat > 0 else 0.0
        yhat_upper = float(stats.poisson.ppf(0.9, mu_hat)) if mu_hat > 0 else 0.0

    return CountModelFit(
        yhat=mu_hat, yhat_lower=yhat_lower, yhat_upper=yhat_upper,
        detail=detail, dispersion_ratio=dispersion_ratio, n_train=n,
    )


# --- Option B: registrable as a deployable champion --------------------------

@dataclass(frozen=True)
class CountModel:
    """Adapter exposing a fitted count model as a Prophet-shaped model:
    `.predict(future) -> DataFrame[["yhat", "yhat_lower", "yhat_upper"]]`.

    Same precomputed-value-at-construction-time design as
    baselines.BaselineModel (see that module's docstring) -- the point
    forecast and interval for the one target period this model was
    built for are computed once, in build_deployable_count_model, and
    `.predict()` just tiles them across however many rows `future` has.
    Unlike BaselineModel's deliberately degenerate interval, this
    interval is a real Poisson/Negative-Binomial quantile range (see
    module docstring) -- `detail` records which distribution produced
    it, for anyone inspecting a deployed artifact.
    """

    detail: str
    yhat: float
    yhat_lower: float
    yhat_upper: float

    def predict(self, future: pd.DataFrame) -> pd.DataFrame:
        n = len(future)
        return pd.DataFrame(
            {
                "yhat": [self.yhat] * n,
                "yhat_lower": [self.yhat_lower] * n,
                "yhat_upper": [self.yhat_upper] * n,
            }
        )


def build_deployable_count_model(
    period_ordinals: Sequence[int],
    train_values: Sequence[float],
    target_period_ordinal: int,
) -> CountModel:
    """Build the deployable CountModel for `models.forecasting.model_registry
    .ALGORITHM_NAME` ("count_model") when it wins champion selection --
    the count-model counterpart to baselines.build_deployable_baseline
    and train_prophet.fit_prophet for the other two cases
    deploy_forecast._build_champion_model dispatches on.

    Refits on the FULL training history passed in (same "refit only the
    winner, on full history" pattern deploy_forecast already applies to
    Prophet) -- the walk-forward fit_and_predict_count_model calls made
    during evaluation used progressively shorter windows; this one uses
    everything available, exactly like train_prophet.fit_prophet does
    for a winning Prophet candidate.
    """
    fit = fit_and_predict_count_model(period_ordinals, train_values, target_period_ordinal)
    return CountModel(
        detail=fit.detail, yhat=fit.yhat, yhat_lower=fit.yhat_lower, yhat_upper=fit.yhat_upper,
    )