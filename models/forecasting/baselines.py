"""
models/forecasting/baselines.py

The two baseline predictors docs/10_Forecasting.md requires every
forecast be compared against, "not optional": naive (last known value)
and historical-average (mean of all training data). If Prophet doesn't
beat these on a given series, that's reported honestly rather than
hidden -- for a small or volatile college, a naive baseline may
legitimately be competitive.
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
