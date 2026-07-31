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
