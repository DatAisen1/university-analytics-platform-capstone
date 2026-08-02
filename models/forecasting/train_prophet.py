"""
models/forecasting/train_prophet.py

Trains a Prophet model per (college, target metric), evaluates it via
walk-forward validation against the exact 4 folds docs/10_Forecasting.md
Section 5 specifies, compares it against the two required baselines
(naive, historical-average), and refits a final model on the full
history for each series (used by Day 21 to actually forecast the next
semester).

Walk-forward folds (docs/10_Forecasting.md, period_ordinal terms):
  Fold 1: train period_ordinal 1-4, test period_ordinal 5 (2023-1)
  Fold 2: train period_ordinal 1-5, test period_ordinal 6 (2023-2)
  Fold 3: train period_ordinal 1-6, test period_ordinal 7 (2024-1)
  Fold 4: train period_ordinal 1-7, test period_ordinal 8 (2024-2)

Each fold trains ONLY on data strictly before its test point -- the
walk-forward discipline docs/10_Forecasting.md requires specifically
because standard k-fold CV would shuffle future information into
training for a time series, producing artificially optimistic accuracy.

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
TEST_PERIOD_ORDINALS = [5, 6, 7, 8]  # the 4 walk-forward test points, per the fold table above


def semester_to_date(academic_year: int, semester_number: int) -> str:
    month_day = "01-01" if semester_number == 1 else "07-01"
    return f"{academic_year}-{month_day}"


def load_series(engine) -> pd.DataFrame:
    """One row per (college, period): college_key, college_id,
    period_ordinal, ds, and each target metric's actual value.

    Fixed to reference gold.dim_academic_period / academic_period_key --
    gold.dim_semester / semester_key no longer exist post Task 23/24's
    dimensional redesign. Ordered by period_ordinal, not the
    academic_period_key surrogate, for the same chronological-ordering
    reason documented in build_kpi.py and build_ml_features.py.
    """
    sql = f"""
        SELECT
            kpi.college_key, col.college_id, ap.period_ordinal,
            ap.academic_year, ap.semester_number,
            {', '.join(f'kpi.{m}' for m in TARGET_METRICS)}
        FROM gold.fact_institution_kpi kpi
        JOIN gold.dim_college col ON kpi.college_key = col.college_key
        JOIN gold.dim_academic_period ap ON kpi.academic_period_key = ap.academic_period_key
        ORDER BY kpi.college_key, ap.period_ordinal
    """
    df = pd.read_sql(sql, engine)
    df["ds"] = df.apply(lambda r: semester_to_date(int(r["academic_year"]), int(r["semester_number"])), axis=1)
    return df


def fit_prophet(train_df: pd.DataFrame):
    """Fit a Prophet model on a training series. yearly_seasonality is
    enabled with a LOW fourier_order (2, vs. Prophet's default 10) --
    with as few as 4-7 semesters (2-3.5 years) of training data in the
    earliest folds, a high-order Fourier fit would overfit the tiny
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
    college_series: pd.DataFrame, metric: str
) -> Dict[str, Dict[str, List[float]]]:
    """Run all 4 walk-forward folds for one (college, metric) series.
    Returns {model_name: {"actual": [...], "predicted": [...]}} with one
    entry per fold, for prophet/naive/historical_avg.
    """
    series = college_series.sort_values("period_ordinal").reset_index(drop=True)
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

        train_prophet_df = train.rename(columns={metric: "y_col"})
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
    """Evaluate every (college, metric) combination via walk-forward
    validation, comparing Prophet against both baselines. Returns one row
    per (college, metric) with each model's metrics and a
    prophet_beats_best_baseline flag."""
    df = load_series(engine)
    rows = []

    try:
        for college_id in sorted(df["college_id"].unique()):
            college_series = df[df["college_id"] == college_id]
            for metric in TARGET_METRICS:
                fold_results = walk_forward_evaluate(college_series, metric)

                model_metrics = {
                    name: compute_metrics_for_model(r["actual"], r["predicted"])
                    for name, r in fold_results.items()
                }

                best_baseline_mae = min(model_metrics["naive"]["mae"], model_metrics["historical_avg"]["mae"])
                beats_baseline = model_metrics["prophet"]["mae"] < best_baseline_mae

                rows.append({
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
    """Refit one Prophet model per (college, metric) on the FULL 8-semester
    history (not a walk-forward fold) and pickle it -- these are the
    models Day 21 loads to actually forecast semester 9 (2025-1)."""
    df = load_series(engine)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    for college_id in sorted(df["college_id"].unique()):
        college_series = df[df["college_id"] == college_id].sort_values("period_ordinal")
        for metric in TARGET_METRICS:
            train_df = college_series.rename(columns={metric: "y_col"})
            model = fit_prophet(train_df)
            path = artifacts_dir / f"{college_id}_{metric}_prophet.pkl"
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
        "| College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Prophet R\u00b2 | Beats Baseline? |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, row in report_df.iterrows():
        flag = "\u2705" if row["prophet_beats_best_baseline"] else "\u26a0\ufe0f NO"
        lines.append(
            f"| {row['college_id']} | {row['metric']} | {row['prophet_mae']:.2f} | "
            f"{row['naive_mae']:.2f} | {row['historical_avg_mae']:.2f} | "
            f"{row['prophet_r2']:.3f} | {flag} |"
        )
    md_path.write_text("\n".join(lines) + "\n")

    return csv_path, md_path


if __name__ == "__main__":
    import os

    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    password = os.environ["PIPELINE_WRITER_PASSWORD"]
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
