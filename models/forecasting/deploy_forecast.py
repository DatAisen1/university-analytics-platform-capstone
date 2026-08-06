"""
models/forecasting/deploy_forecast.py

Day 21 write-back job, built around Task 39's Champion -> Candidate ->
Evaluate -> Compare -> Promote workflow, extended by Task 40-42 (Model
Versioning):

  Champion -> RETRAIN GATE (Task 42: only proceed if there is genuinely
  new academic-period data since the last training run for this
  series, not merely a changed row count) -> Candidate (trained with
  full Task 40 provenance recorded) -> walk-forward EVALUATE (reusing
  train_prophet.walk_forward_evaluate/compute_metrics_for_model,
  unchanged) -> COMPARE against the current CHAMPION -> PROMOTE only if
  models.forecasting.model_registry.decide_promotion says yes.

Only a promoted model ever gets a fresh forecast written to
gold.fact_forecast. A rejected candidate, and a series skipped by the
retrain gate, are both recorded/logged for audit but change nothing in
production -- see model_registry.py's module docstring for the full
Task 39-42 rationale.

One entry point, `deploy_forecasts(engine)`, does this for every
(college, metric) series and returns a per-series decision log.

KNOWN LIMITATION (not worked around here -- see
warehouse/ddl/008_forecast_registry.sql's module docstring for the full
explanation): the forecasted target period (period_ordinal 6, i.e.
2024-1) has no corresponding row in gold.dim_academic_period, because
that dimension is built only from the closed, observed
ACADEMIC_YEARS = [2021, 2022, 2023] range
(pipelines/gold/build_dimensions.py). gold.fact_forecast therefore
stores target_academic_year/target_semester_number/target_period_ordinal
as plain columns rather than an academic_period_key FK.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

from models.forecasting.model_registry import (
    CandidateMetrics,
    PromotionDecision,
    RetrainDecision,
    TrainingMetadata,
    decide_promotion,
    get_current_champion,
    get_last_trained_period_ordinal,
    make_model_version,
    record_candidate,
    should_retrain,
)
from models.forecasting.train_prophet import (
    DEFAULT_ARTIFACTS_DIR,
    TARGET_METRICS,
    compute_metrics_for_model,
    fit_prophet,
    load_series,
    walk_forward_evaluate,
)
from pipelines.common.errors import ForecastError

logger = logging.getLogger(__name__)

# Matches pipelines/gold/build_dimensions.py::period_ordinal's convention
# exactly (0-based, (year - 2021) * 2 + (semester_number - 1)) -- the
# SAME formula, not a reimplementation that could drift from it.
_BASE_ACADEMIC_YEAR = 2021
_ALGORITHM = "prophet"


def _period_ordinal(academic_year: int, semester_number: int) -> int:
    return (academic_year - _BASE_ACADEMIC_YEAR) * 2 + (semester_number - 1)


def _next_target_period(max_observed_ordinal: int) -> tuple[int, int, int]:
    """Given the highest period_ordinal present in the observed data,
    returns (target_academic_year, target_semester_number, target_period_ordinal)
    for the immediately-following semester."""
    next_ordinal = max_observed_ordinal + 1
    academic_year = _BASE_ACADEMIC_YEAR + next_ordinal // 2
    semester_number = 1 if next_ordinal % 2 == 0 else 2
    assert _period_ordinal(academic_year, semester_number) == next_ordinal
    return academic_year, semester_number, next_ordinal


@dataclass(frozen=True)
class DeploymentResult:
    college_id: str
    college_key: int
    metric: str
    retrained: bool          # Task 42: False means the retrain gate skipped this series entirely
    promoted: bool
    reason: str
    model_version: Optional[str] = None
    candidate_mae: Optional[float] = None
    target_academic_year: Optional[int] = None
    target_semester_number: Optional[int] = None
    yhat: Optional[float] = None


def _forecast_next_period(model, target_ds: str) -> tuple[float, float, float]:
    """Point forecast + Prophet's 80% CI, clipped to non-negative --
    Day 21's validation checklist requires plausible (non-negative)
    forecast values."""
    future = pd.DataFrame({"ds": [target_ds]})
    forecast = model.predict(future)
    row = forecast.iloc[0]
    yhat = max(0.0, float(row["yhat"]))
    yhat_lower = max(0.0, float(row["yhat_lower"]))
    yhat_upper = max(0.0, float(row["yhat_upper"]))
    return yhat, yhat_lower, yhat_upper


def _semester_to_date(academic_year: int, semester_number: int) -> str:
    month_day = "01-01" if semester_number == 1 else "07-01"
    return f"{academic_year}-{month_day}"


def _write_forecast_row(
    engine,
    college_key: int,
    metric: str,
    target_academic_year: int,
    target_semester_number: int,
    target_period_ordinal: int,
    model_registry_key: int,
    model_version: str,
    yhat: float,
    yhat_lower: float,
    yhat_upper: float,
) -> None:
    conn = engine.raw_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gold.fact_forecast (
                    college_key, metric, target_academic_year, target_semester_number,
                    target_period_ordinal, model_registry_key, model_version,
                    yhat, yhat_lower, yhat_upper
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (college_key, metric, target_period_ordinal, model_version)
                DO UPDATE SET
                    yhat = EXCLUDED.yhat,
                    yhat_lower = EXCLUDED.yhat_lower,
                    yhat_upper = EXCLUDED.yhat_upper,
                    generated_at = now()
                """,
                (
                    college_key,
                    metric,
                    target_academic_year,
                    target_semester_number,
                    target_period_ordinal,
                    model_registry_key,
                    model_version,
                    yhat,
                    yhat_lower,
                    yhat_upper,
                ),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise ForecastError(
            f"Failed to write forecast row: {exc}", stage="Forecast Deployment",
        ) from exc
    finally:
        conn.close()


def deploy_forecasts(engine, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> List[DeploymentResult]:
    """Runs the full Retrain-gate -> Candidate -> Evaluate -> Compare ->
    Promote workflow for every (college, metric) series and returns one
    DeploymentResult per series.

    Task 42: for each series, should_retrain() is checked FIRST, before
    any walk-forward evaluation or model fitting is even attempted --
    a series with no genuinely new academic period since its last
    training run does no work at all, not just "no promotion".
    """
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        series_df = load_series(engine)
        results: List[DeploymentResult] = []

        for college_id in sorted(series_df["college_id"].unique()):
            college_series = series_df[series_df["college_id"] == college_id].sort_values("period_ordinal")
            college_key = int(college_series["college_key"].iloc[0])
            current_max_period_ordinal = int(college_series["period_ordinal"].max())
            current_min_period_ordinal = int(college_series["period_ordinal"].min())

            for metric in TARGET_METRICS:
                last_trained_period_ordinal = get_last_trained_period_ordinal(engine, college_key, metric)
                retrain_decision: RetrainDecision = should_retrain(current_max_period_ordinal, last_trained_period_ordinal)

                if not retrain_decision.should_retrain:
                    logger.info("Skipping %s/%s (no retrain): %s", college_id, metric, retrain_decision.reason)
                    results.append(
                        DeploymentResult(
                            college_id=college_id, college_key=college_key, metric=metric,
                            retrained=False, promoted=False, reason=retrain_decision.reason,
                        )
                    )
                    continue

                # Per-series walk-forward evaluation (Day 20 harness, unchanged).
                fold_results = walk_forward_evaluate(college_series, metric)
                model_metrics = {
                    name: compute_metrics_for_model(r["actual"], r["predicted"]) for name, r in fold_results.items()
                }
                best_baseline_mae = min(model_metrics["naive"]["mae"], model_metrics["historical_avg"]["mae"])
                beats_baseline = model_metrics["prophet"]["mae"] < best_baseline_mae

                prophet_mape = model_metrics["prophet"]["mape"]
                candidate = CandidateMetrics(
                    mae=model_metrics["prophet"]["mae"],
                    rmse=model_metrics["prophet"]["rmse"],
                    mape=None if pd.isna(prophet_mape) else float(prophet_mape),
                    r2=model_metrics["prophet"]["r2"],
                    best_baseline_mae=best_baseline_mae,
                    beats_baseline=beats_baseline,
                )

                champion = get_current_champion(engine, college_key, metric)
                decision: PromotionDecision = decide_promotion(candidate, champion)
                model_version = make_model_version(college_id, metric)

                # Refit on the FULL history regardless of the decision -- an
                # evaluation-only candidate still needs an artifact on disk
                # so its walk-forward result is reproducible/inspectable
                # later, even if it's never deployed.
                train_df = college_series.rename(columns={metric: "y_col"})
                model = fit_prophet(train_df)
                artifact_path = artifacts_dir / f"{model_version}.pkl"
                with artifact_path.open("wb") as f:
                    pickle.dump(model, f)

                training_meta = TrainingMetadata(
                    algorithm=_ALGORITHM,
                    training_data_start_period_ordinal=current_min_period_ordinal,
                    training_data_end_period_ordinal=current_max_period_ordinal,
                    training_record_count=len(train_df),
                )

                model_registry_key = record_candidate(
                    engine, college_key, metric, model_version, candidate, training_meta, str(artifact_path), decision
                )

                result_kwargs = dict(
                    college_id=college_id,
                    college_key=college_key,
                    metric=metric,
                    retrained=True,
                    model_version=model_version,
                    promoted=decision.promote,
                    reason=decision.reason,
                    candidate_mae=candidate.mae,
                )

                if not decision.promote:
                    logger.warning("Candidate %s NOT promoted: %s", model_version, decision.reason)
                    results.append(DeploymentResult(**result_kwargs))
                    continue

                target_year, target_semester, target_ordinal = _next_target_period(current_max_period_ordinal)
                target_ds = _semester_to_date(target_year, target_semester)
                yhat, yhat_lower, yhat_upper = _forecast_next_period(model, target_ds)

                _write_forecast_row(
                    engine,
                    college_key=college_key,
                    metric=metric,
                    target_academic_year=target_year,
                    target_semester_number=target_semester,
                    target_period_ordinal=target_ordinal,
                    model_registry_key=model_registry_key,
                    model_version=model_version,
                    yhat=yhat,
                    yhat_lower=yhat_lower,
                    yhat_upper=yhat_upper,
                )
                logger.info(
                    "Promoted %s (MAE %.4f): forecast %.2f for %s-%s",
                    model_version, candidate.mae, yhat, target_year, target_semester,
                )

                results.append(
                    DeploymentResult(
                        target_academic_year=target_year,
                        target_semester_number=target_semester,
                        yhat=yhat,
                        **result_kwargs,
                    )
                )

        return results
    except ForecastError:
        raise
    except Exception as exc:
        raise ForecastError(f"Forecast deployment failed: {exc}", stage="Forecast Deployment") from exc


if __name__ == "__main__":
    import os

    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    logging.basicConfig(level=logging.INFO)
    password = os.environ["PIPELINE_WRITER_PASSWORD"]
    engine = build_pipeline_writer_engine(password)

    print("Running retrain-gated champion/candidate/promote forecast deployment...")
    outcomes = deploy_forecasts(engine)
    retrained = [r for r in outcomes if r.retrained]
    skipped = [r for r in outcomes if not r.retrained]
    promoted = [r for r in retrained if r.promoted]
    rejected = [r for r in retrained if not r.promoted]
    print(
        f"{len(retrained)}/{len(outcomes)} series retrained ({len(skipped)} skipped, no new data); "
        f"{len(promoted)}/{len(retrained)} retrained candidates promoted."
    )
    for r in skipped:
        print(f"  SKIPPED {r.college_id}/{r.metric}: {r.reason}")
    for r in rejected:
        print(f"  REJECTED {r.college_id}/{r.metric}: {r.reason}")