"""
models/forecasting/baselines.py

The baseline predictors every Prophet forecast is compared against
(P1.15-P1.17): naive (last known value), historical-average (mean of
all training data), and seasonal-naive (same semester one year prior).
If Prophet doesn't beat the best of these on a given series, that's
reported honestly rather than hidden -- for a small or volatile
program, a simple baseline may legitimately be competitive.

P1 Graduation_count reporting honesty -- Option B (registrable baseline
champions): a walk-forward-winning baseline is no longer just "the bar
Prophet has to clear." models.forecasting.model_registry.select_champion_algorithm
can now pick naive/historical_avg/seasonal_naive as the deployed
champion for a series, exactly like it would pick Prophet. For that to
work end to end, a baseline needs to expose the same minimal interface
Prophet's fitted model exposes -- `.predict(future_df) -> DataFrame`
with yhat/yhat_lower/yhat_upper columns -- so
models/forecasting/deploy_forecast.py's single `_forecast_next_period()`
call site keeps working unchanged regardless of which algorithm won.
`BaselineModel` (below) is that adapter, and `build_deployable_baseline`
constructs one from whichever of the three baseline functions in this
module actually won.

Prediction-interval policy for a deployed baseline (a real decision,
not an oversight -- see the "Option B" writeup this module implements):
`yhat_lower = yhat_upper = yhat`, a degenerate interval. A persistence
or running-average forecast has no principled distribution to derive an
80% CI from without inventing one; reporting a manufactured-looking
confidence band would be less honest than disclosing that this
particular champion doesn't have one. Prophet-champion series are
unaffected -- they keep Prophet's own fitted interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd


def naive_baseline(train_values: Sequence[float]) -> float:
    """Predict the next value as simply the last observed value."""
    if len(train_values) == 0:
        raise ValueError("naive_baseline requires at least one training value")
    return float(train_values[-1])


def historical_average_baseline(train_values: Sequence[float]) -> float:
    """Predict the next value as the mean of all training values seen so far."""
    if len(train_values) == 0:
        raise ValueError("historical_average_baseline requires at least one training value")
    return float(sum(train_values) / len(train_values))


def seasonal_naive_baseline(
    period_ordinals: Sequence[int],
    train_values: Sequence[float],
    target_period_ordinal: int,
    season_length: int = 2,
) -> float:
    """Predict the next value as the value observed at the same point in
    the previous seasonal cycle -- e.g. this semester's forecast is last
    year's same semester, not last semester (that's naive_baseline).

    season_length=2 matches this project's semester grain (2 semesters
    per academic year): the "equivalent seasonal period" for
    target_period_ordinal is target_period_ordinal - season_length.

    A pure function -- it does not decide whether it's *safe* to call
    (that's the caller's job, same division of responsibility as
    to_prophet_frame). Raises ValueError rather than silently falling
    back to another baseline if the required prior-season period isn't
    present in the given training window, so a caller that skips this
    check doesn't get a quietly wrong prediction.
    """
    if len(period_ordinals) != len(train_values):
        raise ValueError(
            "seasonal_naive_baseline requires period_ordinals and train_values "
            f"of equal length (got {len(period_ordinals)} and {len(train_values)})"
        )
    required_ordinal = target_period_ordinal - season_length
    for ordinal, value in zip(period_ordinals, train_values):
        if ordinal == required_ordinal:
            return float(value)
    raise ValueError(
        f"seasonal_naive_baseline: no training value at period_ordinal "
        f"{required_ordinal} (target {target_period_ordinal} - season_length "
        f"{season_length}) -- insufficient seasonal history for this series"
    )


# --- Option B: baselines as deployable champions ----------------------------

#: The algorithm-name strings this module can build a deployable model for.
#: Kept as a literal tuple (not derived from a dict) so an unsupported name
#: fails loudly in build_deployable_baseline rather than silently.
BASELINE_ALGORITHMS = ("naive", "historical_avg", "seasonal_naive")


@dataclass(frozen=True)
class BaselineModel:
    """Adapter exposing a trained baseline as a Prophet-shaped model:
    `.predict(future_df) -> DataFrame[["yhat", "yhat_lower", "yhat_upper"]]`.

    See module docstring for the deliberate degenerate-interval policy
    (`yhat_lower == yhat_upper == yhat`) this implements.
    """

    algorithm: str
    value: float

    def predict(self, future: pd.DataFrame) -> pd.DataFrame:
        n = len(future)
        return pd.DataFrame(
            {
                "yhat": [self.value] * n,
                "yhat_lower": [self.value] * n,
                "yhat_upper": [self.value] * n,
            }
        )


def build_deployable_baseline(
    algorithm: str,
    period_ordinals: Sequence[int],
    train_values: Sequence[float],
    target_period_ordinal: int,
    season_length: int = 2,
) -> BaselineModel:
    """Build the deployable BaselineModel for whichever baseline algorithm
    won champion selection this cycle -- the Option B counterpart to
    `train_prophet.fit_prophet` for the non-Prophet case.

    Raises ValueError (uncaught here -- the caller decides how to react,
    e.g. falling back to the next-best algorithm) in the same case
    `seasonal_naive_baseline` itself raises: the required prior-season
    training value isn't present. Prophet's fit call can raise too
    (ModelTrainingError) -- this isn't a new failure mode, just a
    baseline-shaped one.
    """
    if algorithm == "naive":
        value = naive_baseline(train_values)
    elif algorithm == "historical_avg":
        value = historical_average_baseline(train_values)
    elif algorithm == "seasonal_naive":
        value = seasonal_naive_baseline(period_ordinals, train_values, target_period_ordinal, season_length)
    else:
        raise ValueError(
            f"build_deployable_baseline: unsupported algorithm '{algorithm}' "
            f"(expected one of {BASELINE_ALGORITHMS} or 'prophet', which this "
            "module does not build -- see train_prophet.fit_prophet)"
        )
    return BaselineModel(algorithm=algorithm, value=value)