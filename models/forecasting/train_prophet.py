"""
models/forecasting/train_prophet.py

Trains a Prophet model per (program, target metric), evaluates it via
walk-forward validation against the exact 3 folds docs/10_Forecasting.md
Section 5 specifies for the canonical 6-semester (2021-2022 through
2023-2024) dataset horizon, compares it against the two required
baselines (naive, historical-average), and refits a final model on the
full history for each series (used by Day 21 to actually forecast the
next semester).

P1 (Data Science Recovery) fix: series now come from
gold.ml_program_forecast_features (pipelines/gold/build_ml_features.py,
Task 31-33's dedicated, leakage-safe, fingerprinted forecast dataset --
grain (college, program, academic_period)), not from a hand-rolled query
against gold.fact_institution_kpi. The KPI fact table is COLLEGE grain
by deliberate design (see build_kpi.py's module docstring) and was never
meant to be the forecasting contract; the feature table already existed
for exactly this purpose but had no consumer until this fix. This also
moves the forecast grain from (college, metric) to (program, metric) --
see migrations/versions/0013_forecast_program_grain.py for the schema
side of this change.

Only 3 walk-forward folds are available under the 6-period grain (down
from 4 under a previous, incorrect 8-semester draft) -- a direct,
disclosed consequence of the academic-calendar fix (docs/10_Forecasting.md
Section 5). With this few folds, per-fold metrics are a point estimate
with wide, disclosed uncertainty, not a precise accuracy figure.

Walk-forward folds (docs/10_Forecasting.md, period_ordinal terms --
period_ordinal is 0-based; see pipelines/gold/build_dimensions.py):
  Fold 1: train period_ordinal 0-2, test period_ordinal 3 (2022-2)
  Fold 2: train period_ordinal 0-3, test period_ordinal 4 (2023-1)
  Fold 3: train period_ordinal 0-4, test period_ordinal 5 (2023-2)

Each fold trains ONLY on data strictly before its test point -- the
walk-forward discipline docs/10_Forecasting.md requires specifically
because standard k-fold CV would shuffle future information into
training for a time series, producing artificially optimistic accuracy.

MIN_HISTORY_PERIODS guard: moving to program grain means many more,
smaller series than college grain (a program can have far fewer students
per semester than a whole college). A series with fewer distinct
periods than Fold 1 requires (period_ordinal 0-2 train, 3 test) has no
usable fold at all -- Prophet.fit on 0-1 rows raises, and computing
metrics over an empty actual/predicted list produces silent NaNs that
would otherwise corrupt decide_promotion's comparisons. Such series are
skipped and reported, not silently dropped or allowed to crash the run.

Semester-to-date mapping: semester_number 1 -> Jan 1 of academic_year,
semester_number 2 -> Jul 1 -- the same convention dim_calendar (Day 12)
already established (Jan-Jun = semester 1, Jul-Dec = semester 2 of the
same calendar year), so Prophet's date axis is consistent with the rest
of the project's calendar semantics rather than an unrelated invention.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from pipelines.common.errors import ModelEvaluationError, ModelTrainingError
from models.forecasting.baselines import historical_average_baseline, naive_baseline
from models.forecasting.metrics import mae, mape, r_squared, rmse

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "forecasting" / "artifacts"

TARGET_METRICS = ["enrollment_count", "graduation_count"]
TEST_PERIOD_ORDINALS = [3, 4, 5]  # the 3 walk-forward test points, per the fold table above

# Fold 1 needs period_ordinal 0-2 (train) + 3 (test) present -- 4 distinct
# periods is the minimum for even one usable fold. Below this, a series
# is skipped rather than fed to Prophet/metrics (see module docstring).
MIN_HISTORY_PERIODS = 4


def semester_to_date(academic_year: int, semester_number: int) -> str:
    month_day = "01-01" if semester_number == 1 else "07-01"
    return f"{academic_year}-{month_day}"


def load_series(engine) -> pd.DataFrame:
    """One row per (program, period): program_key, program_id,
    college_key, college_id, period_ordinal, ds, and each target
    metric's actual value.

    P1 fix: reads gold.ml_program_forecast_features -- the dedicated,
    leakage-safe forecast dataset (pipelines/gold/build_ml_features.py) --
    instead of querying gold.fact_institution_kpi (college grain)
    directly. Only the raw target columns are selected here; the
    feature table's own lag/rolling/trend columns are not used as
    Prophet regressors (Prophet's univariate ds/y fit is unchanged) --
    consuming them is a separate, future enhancement, not required to
    fix the "arbitrary warehouse table" contract violation this task
    addresses.

    Ordered by period_ordinal, not the academic_period_key surrogate,
    for the same chronological-ordering reason documented in
    build_kpi.py and build_ml_features.py.
    """
    sql = f"""
        SELECT
            mf.program_key, p.program_id, mf.college_key, col.college_id,
            mf.period_ordinal, mf.academic_year, mf.semester_number,
            {', '.join(f'mf.{m}' for m in TARGET_METRICS)}
        FROM gold.ml_program_forecast_features mf
        JOIN gold.dim_program p ON mf.program_key = p.program_key
        JOIN gold.dim_college col ON mf.college_key = col.college_key
        ORDER BY mf.program_key, mf.period_ordinal
    """
    df = pd.read_sql(sql, engine)
    df["ds"] = df.apply(lambda r: semester_to_date(int(r["academic_year"]), int(r["semester_number"])), axis=1)
    return df


def has_sufficient_history(series: pd.DataFrame) -> bool:
    """True iff `series` (already filtered to one program) has enough
    distinct periods for at least one walk-forward fold. See
    MIN_HISTORY_PERIODS docstring above."""
    return series["period_ordinal"].nunique() >= MIN_HISTORY_PERIODS


def fit_prophet(train_df: pd.DataFrame):
    """Fit a Prophet model on a training series. yearly_seasonality is
    enabled with a LOW fourier_order (2, vs. Prophet's default 10) --
    with as few as 3 semesters (1.5 years) of training data in the
    earliest fold, a high-order Fourier fit would overfit the tiny
    amount of seasonal signal available. Weekly/daily seasonality are
    disabled outright: this is semester-grain data, and fitting
    sub-semester seasonality to it is fitting noise by definition.
    """
    from prophet import Prophet
    try:
        model = Prophet()
        model.fit(train_df)
        return model
    except Exception as exc:
        raise ModelTrainingError(
            f"Prophet training failed: {exc}", stage="Model Training",
            rows_affected=len(train_df),
        ) from exc


def predict_point(model, ds: str) -> float:
    future = pd.DataFrame({"ds": [ds]})
    forecast = model.predict(future)
    return float(forecast["yhat"].iloc[0])


def walk_forward_evaluate(
    entity_series: pd.DataFrame, metric: str
) -> Dict[str, Dict[str, List[float]]]:
    """Run all 3 walk-forward folds for one (program, metric) series.
    Grain-agnostic by construction (only reads period_ordinal/ds/metric
    columns) -- callers decide what `entity_series` is filtered to.
    Returns {model_name: {"actual": [...], "predicted": [...]}} with one
    entry per fold, for prophet/naive/historical_avg.
    """
    series = entity_series.sort_values("period_ordinal").reset_index(drop=True)
    results: Dict[str, Dict[str, List[float]]] = {
        "prophet": {"actual": [], "predicted": []},
        "naive": {"actual": [], "predicted": []},
        "historical_avg": {"actual": [], "predicted": []},
    }

    for test_key in TEST_PERIOD_ORDINALS:
        train = series[series["period_ordinal"] < test_key]
        test_row = series[series["period_ordinal"] == test_key]
        if train.empty or test_row.empty:
            continue

        actual = float(test_row[metric].iloc[0])
        test_ds = test_row["ds"].iloc[0]

        train_prophet_df = train.rename(columns={metric: "y"})
        model = fit_prophet(train_prophet_df)
        prophet_pred = predict_point(model, test_ds)

        train_values = train[metric].tolist()
        naive_pred = naive_baseline(train_values)
        hist_avg_pred = historical_average_baseline(train_values)

        results["prophet"]["actual"].append(actual)
        results["prophet"]["predicted"].append(prophet_pred)
        results["naive"]["actual"].append(actual)
        results["naive"]["predicted"].append(naive_pred)
        results["historical_avg"]["actual"].append(actual)
        results["historical_avg"]["predicted"].append(hist_avg_pred)

    return results


def compute_metrics_for_model(actual: List[float], predicted: List[float]) -> Dict[str, float]:
    result = {"mae": mae(actual, predicted), "rmse": rmse(actual, predicted), "r2": r_squared(actual, predicted)}
    try:
        result["mape"] = mape(actual, predicted)
    except ValueError:
        result["mape"] = float("nan")  # every actual value was 0 across all folds -- disclosed, not hidden
    return result


def evaluate_all_series(engine) -> pd.DataFrame:
    """Evaluate every (program, metric) combination via walk-forward
    validation, comparing Prophet against both baselines. Returns one row
    per (program, metric) with each model's metrics and a
    prophet_beats_best_baseline flag.

    Programs with fewer than MIN_HISTORY_PERIODS distinct periods are
    skipped (logged, not silently dropped) -- see module docstring."""
    df = load_series(engine)
    rows = []

    try:
        for program_id in sorted(df["program_id"].unique()):
            program_series = df[df["program_id"] == program_id]
            if not has_sufficient_history(program_series):
                logging.getLogger(__name__).info(
                    "Skipping program %s: only %d distinct period(s), need >= %d for one walk-forward fold",
                    program_id, program_series["period_ordinal"].nunique(), MIN_HISTORY_PERIODS,
                )
                continue

            college_id = program_series["college_id"].iloc[0]
            for metric in TARGET_METRICS:
                fold_results = walk_forward_evaluate(program_series, metric)

                model_metrics = {
                    name: compute_metrics_for_model(r["actual"], r["predicted"])
                    for name, r in fold_results.items()
                }

                best_baseline_mae = min(model_metrics["naive"]["mae"], model_metrics["historical_avg"]["mae"])
                beats_baseline = model_metrics["prophet"]["mae"] < best_baseline_mae

                rows.append({
                    "program_id": program_id,
                    "college_id": college_id,
                    "metric": metric,
                    "prophet_mae": model_metrics["prophet"]["mae"],
                    "prophet_rmse": model_metrics["prophet"]["rmse"],
                    "prophet_mape": model_metrics["prophet"]["mape"],
                    "prophet_r2": model_metrics["prophet"]["r2"],
                    "naive_mae": model_metrics["naive"]["mae"],
                    "historical_avg_mae": model_metrics["historical_avg"]["mae"],
                    "best_baseline_mae": best_baseline_mae,
                    "prophet_beats_best_baseline": beats_baseline,
                })

        return pd.DataFrame(rows)
    except Exception as exc:
        raise ModelEvaluationError(
            f"Walk-forward evaluation failed: {exc}", stage="Model Evaluation",
        ) from exc


def train_final_models(engine, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> List[str]:
    """Refit one Prophet model per (program, metric) on the FULL 6-semester
    history (not a walk-forward fold) and pickle it -- these are the
    models Day 21 loads to actually forecast period_ordinal 6 (2024-1).

    Programs with fewer than MIN_HISTORY_PERIODS distinct periods are
    skipped -- same guard as evaluate_all_series, for the same reason
    (Prophet.fit needs at least 2 rows; a fold-1-eligible series has more)."""
    df = load_series(engine)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    for program_id in sorted(df["program_id"].unique()):
        program_series = df[df["program_id"] == program_id].sort_values("period_ordinal")
        if not has_sufficient_history(program_series):
            continue
        for metric in TARGET_METRICS:
            train_df = program_series.rename(columns={metric: "y"})
            model = fit_prophet(train_df)
            path = artifacts_dir / f"{program_id}_{metric}_prophet.pkl"
            with path.open("wb") as f:
                pickle.dump(model, f)
            saved_paths.append(str(path))

    return saved_paths


def write_evaluation_report(report_df: pd.DataFrame, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> Tuple[Path, Path]:
    """Write the evaluation report as both CSV (for programmatic use) and
    a human-readable Markdown summary (Day 20's deliverable)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    csv_path = artifacts_dir / "evaluation_report.csv"
    md_path = artifacts_dir / "evaluation_report.md"

    report_df.to_csv(csv_path, index=False)

    total = len(report_df)
    beats_count = int(report_df["prophet_beats_best_baseline"].sum())
    lines = [
        "# Forecast Model Evaluation Report",
        "",
        f"Prophet beats the best baseline on **{beats_count} of {total}** series "
        f"({beats_count / total:.0%}).",
        "",
        "| Program | College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Prophet R\u00b2 | Beats Baseline? |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, row in report_df.iterrows():
        flag = "\u2705" if row["prophet_beats_best_baseline"] else "\u26a0\ufe0f NO"
        lines.append(
            f"| {row['program_id']} | {row['college_id']} | {row['metric']} | {row['prophet_mae']:.2f} | "
            f"{row['naive_mae']:.2f} | {row['historical_avg_mae']:.2f} | "
            f"{row['prophet_r2']:.3f} | {flag} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return csv_path, md_path


if __name__ == "__main__":
    from pipelines.common.settings import get_postgres_settings
    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)

    print("Running walk-forward evaluation...")
    report = evaluate_all_series(engine)
    csv_path, md_path = write_evaluation_report(report)
    beats = int(report["prophet_beats_best_baseline"].sum())
    print(f"Evaluation complete: Prophet beats baseline on {beats}/{len(report)} series.")
    print(f"Report written to {csv_path} and {md_path}")

    print("Training final models on full history...")
    paths = train_final_models(engine)
    print(f"Saved {len(paths)} model artifacts to {DEFAULT_ARTIFACTS_DIR}")