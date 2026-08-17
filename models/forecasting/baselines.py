"""
models/forecasting/baselines.py

The baseline predictors every Prophet forecast is compared against
(P1.15-P1.17): naive (last known value), historical-average (mean of
all training data), and seasonal-naive (same semester one year prior).
If Prophet doesn't beat the best of these on a given series, that's
reported honestly rather than hidden -- for a small or volatile
program, a simple baseline may legitimately be competitive.
"""

from __future__ import annotations

from typing import Sequence


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