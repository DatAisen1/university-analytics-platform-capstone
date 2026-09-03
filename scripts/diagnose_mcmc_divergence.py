"""
scripts/diagnose_mcmc_divergence.py

Root-causes the 0/222 "MCMC calibrated" result in a real
evaluation_report.md run (docs/22_Interval_Calibration_Resolution.md).
Every walk-forward fold under the 10-period dataset has n_train in
{7, 8, 9} -- all >= MCMC_MIN_TRAIN_POINTS=7 -- so every fold should
ATTEMPT real MCMC sampling, per fit_prophet() in
models/forecasting/train_prophet.py. If literally all 222 come back
"not calibrated" with no variation by series, that's far more likely to
be a diagnostic-reading failure (model.stan_backend.stan_fit.divergences
not existing/behaving as expected on whatever cmdstanpy version is
actually installed -- it's an UNPINNED transitive dependency of
prophet==1.1.5 in requirements.txt) than 222 independent real
divergences, which would contradict this project's own benchmark
(0% divergence at n>=7 across 9 varied trials).

Deliberately standalone: no Postgres, no Dagster, no full pipeline --
just Prophet + cmdstanpy directly, so this runs in seconds and isolates
the MCMC/diagnostics question from everything else that could be wrong
in a full run.

Usage:
    python3 scripts/diagnose_mcmc_divergence.py

Exit code 0: diagnostics ARE readable here (prints what a real
             divergence count looks like, and cross-checks it against
             the project's own _mcmc_divergent_transitions()).
Exit code 1: diagnostics could NOT be read -- prints the real
             exception (type + message) that the project's `except
             (AttributeError, TypeError): return None` currently
             swallows silently, plus environment info to compare
             against whatever machine generated evaluation_report.md.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def _make_series(n_points: int, seed: int) -> pd.DataFrame:
    """A synthetic series the same size as a real walk-forward fold's
    training window (7, 8, or 9 points under the 10-period dataset),
    with enough trend + noise to look like a real enrollment/graduation
    series, not a degenerate all-same-value one that might legitimately
    make MCMC's job trivial (and mask a real problem)."""
    rng = np.random.default_rng(seed)
    trend = np.linspace(20, 40, n_points)
    noise = rng.normal(0, 3, n_points)
    return pd.DataFrame({
        "ds": pd.date_range("2021-01-01", periods=n_points, freq="6MS"),
        "y": np.maximum(trend + noise, 0),
    })


def main() -> int:
    print("== Environment ==")
    import prophet
    import cmdstanpy
    print(f"prophet: {prophet.__version__}")
    print(f"cmdstanpy: {cmdstanpy.__version__}")
    try:
        print(f"cmdstan_path: {cmdstanpy.cmdstan_path()}")
    except Exception as exc:  # noqa: BLE001
        print(f"cmdstan_path(): {type(exc).__name__}: {exc}")
    print()

    from prophet import Prophet

    sys.path.insert(0, ".")
    from models.forecasting.train_prophet import (
        MCMC_SAMPLES,
        YEARLY_SEASONALITY_FOURIER_ORDER,
        _mcmc_divergent_transitions,
    )

    overall_ok = True

    for n_points in (7, 8, 9):
        print(f"== n_train = {n_points} (matches a real walk-forward fold) ==")
        df = _make_series(n_points, seed=n_points)

        try:
            model = Prophet(
                yearly_seasonality=YEARLY_SEASONALITY_FOURIER_ORDER,
                weekly_seasonality=False,
                daily_seasonality=False,
                mcmc_samples=MCMC_SAMPLES,
            )
            model.fit(df)
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            print(f"  PROPHET CONSTRUCTION/FIT FAILED: {type(exc).__name__}: {exc}")
            print(
                "  (this is a different, earlier failure than the divergence "
                "question -- CmdStan itself isn't usable here. Run "
                "scripts/verify_cmdstan.py first.)"
            )
            print()
            continue

        # Step 1: what does the RAW attribute chain give us, with the
        # real exception visible instead of caught?
        try:
            raw = model.stan_backend.stan_fit.divergences
            print(f"  model.stan_backend.stan_fit.divergences -> {raw!r} (type={type(raw).__name__})")
            if raw is not None:
                print(f"  sum(divergences) = {int(sum(raw))}")
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            print(f"  RAW ACCESS FAILED: {type(exc).__name__}: {exc}")
            print(f"  type(model.stan_backend) = {type(model.stan_backend)}")
            print(f"  type(model.stan_backend.stan_fit) = {type(getattr(model.stan_backend, 'stan_fit', None))}")
            print(f"  dir(model.stan_backend.stan_fit) sample: "
                  f"{[a for a in dir(getattr(model.stan_backend, 'stan_fit', object())) if 'diverg' in a.lower()]}")

        # Step 2: what does the PROJECT'S OWN function return, using
        # the exact same try/except it uses in production?
        project_result = _mcmc_divergent_transitions(model)
        print(f"  _mcmc_divergent_transitions(model) -> {project_result!r}")
        print()

    print("== Verdict ==")
    if overall_ok:
        print(
            "Raw divergence access worked in all 3 cases -- if your real "
            "evaluation_report.md run still shows 0/222 calibrated, the gap "
            "is likely environment-specific (different cmdstanpy version on "
            "that machine, or a different code path than fit_prophet() is "
            "hit at runtime). Compare the prophet/cmdstanpy versions printed "
            "above against the machine that produced that report."
        )
        return 0
    else:
        print(
            "Raw access FAILED above -- this is almost certainly why "
            "_mcmc_divergent_transitions() returns None for every fold, "
            "which the project code treats as 'could not verify "
            "convergence' and falls back to MAP (calibrated=False). Pin "
            "cmdstanpy in requirements.txt to a version confirmed to expose "
            "`.divergences` at this path, or update "
            "_mcmc_divergent_transitions() to read whatever the installed "
            "version actually exposes (see the dir() listing above for "
            "candidates)."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())