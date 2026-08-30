"""
models/forecasting/metrics.py

Pure evaluation metric functions -- MAE, RMSE, MAPE, R^2 -- per
docs/10_Forecasting.md Section 5. Kept separate from the training/
evaluation harness (train_prophet.py) for the same reason every rules
module in this project is separate from its orchestration: plain values
in, plain values out, independently testable against hand-computed
examples (Day 20's testing checklist).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Mean Absolute Error -- directly interpretable in the original
    units (e.g. 'off by ~12 students on average')."""
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Root Mean Squared Error -- like MAE but penalizes large individual
    misses more heavily, surfacing occasional big errors MAE would hide."""
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mape(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Mean Absolute Percentage Error -- comparable across small and
    large series (a small college and CICT aren't penalized on the same
    absolute scale). Undefined where y_true is 0; those points are
    excluded from the mean rather than producing inf/NaN silently.
    """
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    mask = y_true != 0
    if not mask.any():
        raise ValueError("MAPE is undefined: every y_true value is 0")
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def r_squared(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Proportion of variance explained, relative to always predicting
    the mean of y_true. With only 4 held-out points per series (this
    project's walk-forward fold count), R^2 is a genuinely unstable
    statistic -- reported anyway, per docs/10_Forecasting.md, but this
    instability is disclosed rather than treated as a precise number.
    """
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        # Every actual value is identical -- R^2 is undefined by the
        # standard formula (0/0). A model matching a constant series
        # exactly is a perfect fit; report 1.0 only in that exact case,
        # otherwise 0.0 (no better than predicting the constant itself).
        return 1.0 if ss_res == 0 else 0.0
    return float(1 - (ss_res / ss_tot))


def interval_coverage(
    y_true: Sequence[float], y_lower: Sequence[float], y_upper: Sequence[float]
) -> tuple[int, int]:
    """(hits, n): how many of n held-out actuals fell within [y_lower,
    y_upper] -- the empirical check on whether a model's stated 80%
    interval actually behaves like an 80% interval on THIS project's real
    fold sizes (as few as 3 held-out points per series; empirical
    coverage at that n is a noisy estimate of the true rate, reported as
    a fraction (x/N), not smoothed into a misleadingly precise
    percentage). A baseline's degenerate y_lower == y_upper == yhat
    interval (see baselines.py's BaselineModel docstring) will show 0/N
    coverage unless a prediction happens to exactly equal the actual --
    that's the honest consequence of reporting no uncertainty, not a
    bug in this function.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_lower = np.asarray(y_lower, dtype=float)
    y_upper = np.asarray(y_upper, dtype=float)
    hits = int(np.sum((y_true >= y_lower) & (y_true <= y_upper)))
    return hits, len(y_true)


def mean_interval_width(y_lower: Sequence[float], y_upper: Sequence[float]) -> float:
    """Average (y_upper - y_lower) across held-out folds, in the metric's
    original units. A model can win on coverage by producing a wide
    enough interval to swallow anything -- this is the companion metric
    that keeps a wide-but-useless interval from looking as good as a
    tight, well-calibrated one; read coverage and width together, never
    coverage alone."""
    y_lower = np.asarray(y_lower, dtype=float)
    y_upper = np.asarray(y_upper, dtype=float)
    return float(np.mean(y_upper - y_lower))


def normalized_interval_width(
    y_lower: Sequence[float], y_upper: Sequence[float], y_true: Sequence[float]
) -> float:
    """mean_interval_width, divided by the mean of the actual values --
    the same small-vs-large-program scale problem MAPE exists to fix for
    point error (§8 of docs/10_Forecasting.md) applies just as much to
    interval width: a graduation_count interval of +/-3 students is huge
    for a program that graduates 2 people a semester and tiny for one
    that graduates 40. Undefined (NaN, not raised) where every y_true
    value is 0 -- the same "disclosed, not hidden" convention
    compute_metrics_for_model already applies to MAPE in that case,
    rather than raising and aborting the whole series' evaluation.
    """
    y_true = np.asarray(y_true, dtype=float)
    mean_actual = float(np.mean(y_true))
    if mean_actual == 0:
        return float("nan")
    return mean_interval_width(y_lower, y_upper) / mean_actual