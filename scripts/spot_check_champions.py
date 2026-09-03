"""
scripts/spot_check_champions.py

P1.3 (Model Selection Robustness): "Manually spot-check champion-selection
results against the numbers -- passing tests confirm the code does what
it was told, not that the choices make sense."

evaluation_report.md only shows summary MAE/RMSE per series. This pulls
the real per-fold actual-vs-predicted-vs-interval detail (walk_forward_evaluate's
raw output) for a curated sample of real series from the live database,
so a human can look at what each algorithm actually predicted, fold by
fold, and judge whether the champion pick makes practical sense -- not
just whether it has the lowest number.

Sample selection, and why each one is here (grounded in the real 10-period
evaluation_report.md and the P1.1 tolerance-flip analysis, not arbitrary):

  - CICT-BSDS / enrollment_count: a clear, large Prophet win (Prophet MAE
    7.28 vs best baseline 36.00). Sanity check: does Prophet's prediction
    actually track the real trend, or does it just have a lucky small
    number?
  - COE-BSEE / enrollment_count: Prophet loses badly here (55.86 vs
    naive's 36.67) -- the report's only clear appears-to-fail case for
    Prophet's MAE not just missing its own interval. What is Prophet
    actually doing wrong on this series?
  - COA-CERT-BUT / graduation_count: tiny-sample certificate program,
    every algorithm's MAE is small and close together (naive 1.67, hist
    avg 1.36, seasonal_naive 2.00, count_model 1.61, prophet 2.88) --
    does "historical_avg wins" mean anything at this sample size, or is
    it noise?
  - The 6 series where P1.1's tolerance change actually flipped the
    champion (see docs -- CICT-BSIT-DB/graduation_count,
    CMBT-BSBA-FM/graduation_count, COA-BSARCH/enrollment_count,
    COA-CERT-CADD/graduation_count, COED-BSNED/enrollment_count,
    CPADM-BPA-DRM/enrollment_count): these are exactly the cases the new
    policy changes the outcome for, so they're the most important ones
    to actually look at, not just count.

Usage:
    python scripts/spot_check_champions.py
"""

from __future__ import annotations

import sys

import pandas as pd

sys.path.insert(0, ".")

from models.forecasting.model_registry import AlgorithmResult, select_champion_algorithm
from models.forecasting.train_prophet import (
    derive_test_period_ordinals,
    load_series,
    walk_forward_evaluate,
)
from pipelines.common.settings import get_postgres_settings
from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

SAMPLE = [
    ("CICT-BSDS", "enrollment_count", "clear Prophet win -- does the trend actually track?"),
    ("COE-BSEE", "enrollment_count", "Prophet's worst clear loss -- what goes wrong?"),
    ("COA-CERT-BUT", "graduation_count", "tiny-sample certificate program -- is the winner meaningful?"),
    ("CICT-BSIT-DB", "graduation_count", "P1.1 tolerance flip: prophet -> naive"),
    ("CMBT-BSBA-FM", "graduation_count", "P1.1 tolerance flip: prophet -> seasonal_naive"),
    ("COA-BSARCH", "enrollment_count", "P1.1 tolerance flip: prophet -> naive"),
    ("COA-CERT-CADD", "graduation_count", "P1.1 tolerance flip: prophet -> seasonal_naive"),
    ("COED-BSNED", "enrollment_count", "P1.1 tolerance flip: prophet -> naive"),
    ("CPADM-BPA-DRM", "enrollment_count", "P1.1 tolerance flip: seasonal_naive -> naive"),
]


def main() -> int:
    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)
    all_series = load_series(engine)

    max_ordinal = int(all_series["period_ordinal"].max())
    test_ordinals = derive_test_period_ordinals(max_ordinal)

    for program_id, metric, why in SAMPLE:
        series = all_series[all_series["program_id"] == program_id]
        if series.empty:
            print(f"=== {program_id} / {metric}: NOT FOUND in gold.ml_program_forecast_features -- skipping ===\n")
            continue

        print(f"=== {program_id} / {metric} ===")
        print(f"    why this one: {why}")

        results = walk_forward_evaluate(series, metric, test_ordinals=test_ordinals)

        # Per-fold table: actual vs. every algorithm's prediction, so a
        # human can see the shape, not just the summary error.
        n_folds = len(results["prophet"]["actual"])
        for fold_idx in range(n_folds):
            actual = results["prophet"]["actual"][fold_idx]
            line = f"    fold {fold_idx + 1}: actual={actual:.1f}"
            for algo in ("prophet", "naive", "historical_avg", "seasonal_naive", "count_model"):
                preds = results[algo]["predicted"]
                if fold_idx < len(preds):
                    line += f"  {algo}={preds[fold_idx]:.1f}"
            print(line)

        # Recompute the champion pick against these real per-fold results,
        # using the actual current-cycle MAE (mean abs error across folds)
        # per algorithm, so this exercises the same select_champion_algorithm
        # a live deploy_forecast run would.
        candidates = []
        for algo in ("prophet", "naive", "historical_avg", "seasonal_naive", "count_model"):
            actuals = results[algo]["actual"]
            preds = results[algo]["predicted"]
            if not actuals:
                continue
            errors = [abs(a - p) for a, p in zip(actuals, preds)]
            mae = sum(errors) / len(errors)
            candidates.append(AlgorithmResult(algorithm=algo, mae=mae, rmse=0.0, mape=None, r2=0.0))

        selection = select_champion_algorithm(candidates)
        print(f"    CHAMPION: {selection.winner.algorithm} -- {selection.reason}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())