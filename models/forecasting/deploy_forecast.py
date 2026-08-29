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
explanation): the forecasted target period (the first period_ordinal past
the observed window's end) has no corresponding row in
gold.dim_academic_period, because that dimension is built only from the
closed, observed ACADEMIC_YEARS range -- pipelines.common.academic_
periods.OBSERVED_ACADEMIC_YEARS, currently 2021-2025 (10 periods; was
2021-2023/6 periods before the P0 Dataset Extension task), imported into
pipelines/gold/build_dimensions.py as ACADEMIC_YEARS. gold.fact_forecast
therefore stores target_academic_year/target_semester_number/
target_period_ordinal as plain columns rather than an academic_period_key
FK.

P1 (Data Science Recovery) fix: every series here is now (program,
metric), not (college, metric) -- train_prophet.load_series() reads
gold.ml_program_forecast_features instead of gold.fact_institution_kpi.
gold.model_registry / gold.fact_forecast were migrated to match
(migrations/versions/0013_forecast_program_grain.py): program_key is
the grain key, college_key is kept only as a denormalized convenience
column sourced from dim_program at write time.

P1 (Forecast Output Contract) fix: every TrainingMetadata built here now
carries dataset_fingerprint (migrations/versions/0014_dataset_fingerprint.py)
-- one fingerprint per deploy_forecasts() run, computed once from the
load_series() pull and reused across every candidate that run trains, so
a forecast row (joined via model_registry_key) can be traced back to the
exact dataset snapshot that produced it, not just the training window it
covered.

P1 Graduation_count reporting honesty -- Option B (registrable baseline
champions, models.forecasting.model_registry.select_champion_algorithm /
decide_champion_promotion): the per-series loop below no longer treats
Prophet as "the candidate" with baselines as a fixed bar it must clear.
Every algorithm with a defined walk-forward MAE this cycle (prophet,
naive, historical_avg, and seasonal_naive when it has an eligible fold)
competes on equal footing; whichever wins is the candidate that gets
recorded and, if it doesn't regress on the existing champion, deployed.
Prophet is only refit on full history (the expensive step) when it
actually wins -- a series where a baseline already wins the walk-forward
comparison has no reason to pay for a Prophet refit nobody will deploy.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

from models.forecasting.baselines import build_deployable_baseline
from models.forecasting.count_model import build_deployable_count_model
from models.forecasting.model_registry import (
    AlgorithmResult,
    CandidateMetrics,
    ChampionSelection,
    PromotionDecision,
    RetrainDecision,
    TrainingMetadata,
    decide_champion_promotion,
    get_current_champion,
    get_last_trained_period_ordinal,
    make_model_version,
    record_candidate,
    select_champion_algorithm,
    should_retrain,
)
from models.forecasting.train_prophet import (
    DEFAULT_ARTIFACTS_DIR,
    MIN_HISTORY_PERIODS,
    SEASON_LENGTH,
    TARGET_METRICS,
    compute_metrics_for_model,
    derive_test_period_ordinals,
    fit_prophet,
    has_sufficient_history,
    load_series,
    to_prophet_frame,
    walk_forward_evaluate,
)
from pipelines.common.errors import ForecastError
from pipelines.gold.build_ml_features import feature_dataset_fingerprint

logger = logging.getLogger(__name__)

# Matches pipelines/gold/build_dimensions.py::period_ordinal's convention
# exactly (0-based, (year - 2021) * 2 + (semester_number - 1)) -- the
# SAME formula, not a reimplementation that could drift from it.
_BASE_ACADEMIC_YEAR = 2021


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
    program_id: str
    program_key: int
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
    # Option B: which algorithm won this cycle's champion selection --
    # 'prophet', 'naive', 'historical_avg', or 'seasonal_naive'. None only
    # when retrained is False (the retrain gate skipped this series before
    # any algorithm was evaluated) or the series lacked sufficient history.
    algorithm: Optional[str] = None


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


def _build_champion_model(algorithm: str, program_series: pd.DataFrame, metric: str, target_period_ordinal: int):
    """Option B: build the deployable model object for whichever algorithm
    won champion selection this cycle, and the training record count to
    log alongside it. Returns (model, training_record_count) where `model`
    exposes `.predict(future_df) -> DataFrame[yhat, yhat_lower, yhat_upper]`
    regardless of algorithm -- Prophet's own model for 'prophet',
    models.forecasting.count_model.CountModel (real Poisson/NB quantile
    interval, see that module's docstring) for 'count_model', and
    baselines.BaselineModel (degenerate interval) for anything else.

    Raises ValueError only for 'seasonal_naive' with no training value at
    the required prior-season period_ordinal -- the caller
    (deploy_forecasts) is responsible for falling back when that happens.
    count_model has no equivalent deployment-time failure mode: unlike
    seasonal_naive's lookback requirement, it always has SOME valid fit
    (degenerate-zero at worst -- see that module's docstring), so it never
    needs the same fallback path.
    """
    if algorithm == "prophet":
        train_df = to_prophet_frame(program_series, metric)
        model = fit_prophet(train_df)
        return model, len(train_df)

    period_ordinals = program_series["period_ordinal"].tolist()
    train_values = program_series[metric].tolist()

    if algorithm == "count_model":
        model = build_deployable_count_model(period_ordinals, train_values, target_period_ordinal)
        return model, len(train_values)

    model = build_deployable_baseline(algorithm, period_ordinals, train_values, target_period_ordinal, SEASON_LENGTH)
    return model, len(train_values)


def _write_forecast_row(
    engine,
    program_key: int,
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
                    program_key, college_key, metric, target_academic_year, target_semester_number,
                    target_period_ordinal, model_registry_key, model_version,
                    yhat, yhat_lower, yhat_upper, forecast_grain
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'program')
                -- Targets ux_fact_forecast_program_grain (migration 0016) explicitly:
                -- a partial unique index requires its WHERE predicate repeated here,
                -- Postgres will not infer it from the index definition alone.
                ON CONFLICT (program_key, metric, target_period_ordinal, model_version)
                    WHERE forecast_grain = 'program'
                DO UPDATE SET
                    yhat = EXCLUDED.yhat,
                    yhat_lower = EXCLUDED.yhat_lower,
                    yhat_upper = EXCLUDED.yhat_upper,
                    generated_at = now()
                """,
                (
                    program_key,
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
    Promote workflow for every (program, metric) series and returns one
    DeploymentResult per series.

    Task 42: for each series, should_retrain() is checked FIRST, before
    any walk-forward evaluation or model fitting is even attempted --
    a series with no genuinely new academic period since its last
    training run does no work at all, not just "no promotion".

    P1 fix: series are now (program, metric) -- see module docstring.
    Programs below MIN_HISTORY_PERIODS distinct periods are skipped with
    a logged reason (same guard as train_prophet.evaluate_all_series),
    since Prophet cannot fit and metrics cannot be computed on a series
    with no usable walk-forward fold.
    """
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        series_df = load_series(engine)
        results: List[DeploymentResult] = []

        # P1 (Forecast Output Contract, dataset_fingerprint): ONE fingerprint
        # for the whole load_series() pull, computed ONCE and reused for
        # every candidate trained in this run -- not per-program -- because
        # every candidate this run trains genuinely was trained against
        # the same query result. Reuses build_ml_features' own hashing
        # function for a consistent method, but is computed over the
        # (program_key, period_ordinal, target metrics, ...) columns
        # load_series() actually selects, not the full lag/rolling feature
        # set gold.ml_program_forecast_features carries -- this fingerprint
        # answers "which load_series() snapshot trained this candidate",
        # not "which full feature-table build" (that already has its own
        # fingerprint, logged in the Dagster asset that builds the table).
        dataset_fingerprint = feature_dataset_fingerprint(series_df) if not series_df.empty else "empty"

        # P1.14 fix: derive walk-forward fold boundaries ONCE from the
        # whole dataset's max observed period_ordinal (mirrors
        # train_prophet.evaluate_all_series) instead of letting each
        # program derive its own from a possibly-shorter series -- a
        # discontinued program's shorter history must still be evaluated
        # against the SAME global fold points as every other program
        # (with unsupported folds skipped, same as before), not against
        # a different set of folds derived from its own truncated max.
        test_ordinals = derive_test_period_ordinals(int(series_df["period_ordinal"].max()))

        for program_id in sorted(series_df["program_id"].unique()):
            program_series = series_df[series_df["program_id"] == program_id].sort_values("period_ordinal")
            program_key = int(program_series["program_key"].iloc[0])
            college_id = program_series["college_id"].iloc[0]
            college_key = int(program_series["college_key"].iloc[0])
            current_max_period_ordinal = int(program_series["period_ordinal"].max())
            current_min_period_ordinal = int(program_series["period_ordinal"].min())

            if not has_sufficient_history(program_series):
                reason = (
                    f"insufficient history: {program_series['period_ordinal'].nunique()} distinct "
                    f"period(s), need >= {MIN_HISTORY_PERIODS} for one walk-forward fold"
                )
                logger.info("Skipping program %s (all metrics): %s", program_id, reason)
                for metric in TARGET_METRICS:
                    results.append(
                        DeploymentResult(
                            program_id=program_id, program_key=program_key,
                            college_id=college_id, college_key=college_key, metric=metric,
                            retrained=False, promoted=False, reason=reason,
                        )
                    )
                continue

            for metric in TARGET_METRICS:
                last_trained_period_ordinal = get_last_trained_period_ordinal(engine, program_key, metric)
                retrain_decision: RetrainDecision = should_retrain(current_max_period_ordinal, last_trained_period_ordinal)

                if not retrain_decision.should_retrain:
                    logger.info("Skipping %s/%s (no retrain): %s", program_id, metric, retrain_decision.reason)
                    results.append(
                        DeploymentResult(
                            program_id=program_id, program_key=program_key,
                            college_id=college_id, college_key=college_key, metric=metric,
                            retrained=False, promoted=False, reason=retrain_decision.reason,
                        )
                    )
                    continue

                # Per-series walk-forward evaluation (Day 20 harness, unchanged),
                # scored against the dataset-wide fold boundaries computed above.
                fold_results = walk_forward_evaluate(program_series, metric, test_ordinals)
                model_metrics = {
                    name: compute_metrics_for_model(r["actual"], r["predicted"]) for name, r in fold_results.items()
                }

                # Option B: every algorithm with a DEFINED walk-forward MAE this
                # cycle is a candidate for champion, not just Prophet.
                # seasonal_naive is excluded when it has zero eligible folds
                # (NaN MAE, same condition evaluate_all_series already checks
                # before folding it into best_baseline_mae) -- a NaN here would
                # otherwise corrupt select_champion_algorithm's ranking.
                algorithm_candidates: List[AlgorithmResult] = []
                for name, m in model_metrics.items():
                    if pd.isna(m["mae"]):
                        continue
                    algorithm_candidates.append(
                        AlgorithmResult(
                            algorithm=name,
                            mae=m["mae"],
                            rmse=m["rmse"],
                            mape=None if pd.isna(m["mape"]) else float(m["mape"]),
                            r2=m["r2"],
                        )
                    )
                selection: ChampionSelection = select_champion_algorithm(algorithm_candidates)

                # best_baseline_mae / beats_baseline are still populated (Task
                # 39/P1.24's original reporting fields, and gold.model_registry
                # NOT NULL columns) -- "best baseline" here means the best of
                # the three baseline algorithms specifically, regardless of
                # which algorithm this cycle's winner turned out to be, so the
                # field keeps its original meaning even when the winner IS a
                # baseline (in which case beats_baseline is correctly False:
                # a baseline doesn't "beat" itself, it simply wins outright).
                baseline_maes = [
                    c.mae for c in algorithm_candidates if c.algorithm in ("naive", "historical_avg", "seasonal_naive")
                ]
                best_baseline_mae = min(baseline_maes) if baseline_maes else selection.winner.mae
                candidate = CandidateMetrics(
                    mae=selection.winner.mae,
                    rmse=selection.winner.rmse,
                    mape=selection.winner.mape,
                    r2=selection.winner.r2,
                    best_baseline_mae=best_baseline_mae,
                    beats_baseline=selection.winner.mae < best_baseline_mae,
                )

                champion = get_current_champion(engine, program_key, metric)
                decision: PromotionDecision = decide_champion_promotion(selection.winner, champion)
                model_version = make_model_version(program_id, metric, selection.winner.algorithm)

                target_year, target_semester, target_ordinal = _next_target_period(current_max_period_ordinal)
                target_ds = _semester_to_date(target_year, target_semester)

                # Build (and pickle) the deployable model for whichever
                # algorithm actually won -- Prophet is only refit on full
                # history here when it won; a baseline win skips that
                # (expensive) refit entirely. See module docstring.
                try:
                    model, training_record_count = _build_champion_model(
                        selection.winner.algorithm, program_series, metric, target_ordinal,
                    )
                except ValueError as exc:
                    # Only seasonal_naive can raise here (missing prior-season
                    # value at the DEPLOYMENT target, a stricter requirement
                    # than "had >=1 eligible fold during walk-forward" -- rare,
                    # but not impossible near MIN_HISTORY_PERIODS). Fall back
                    # to the next-best already-ranked algorithm rather than
                    # failing the whole series.
                    logger.warning(
                        "%s/%s: winning algorithm %s could not be materialized for "
                        "deployment (%s); falling back to next-best algorithm",
                        program_id, metric, selection.winner.algorithm, exc,
                    )
                    remaining = [c for c in selection.ranked if c.algorithm != selection.winner.algorithm]
                    selection = select_champion_algorithm(remaining)
                    model_version = make_model_version(program_id, metric, selection.winner.algorithm)
                    model, training_record_count = _build_champion_model(
                        selection.winner.algorithm, program_series, metric, target_ordinal,
                    )
                    decision = decide_champion_promotion(selection.winner, champion)
                    candidate = CandidateMetrics(
                        mae=selection.winner.mae, rmse=selection.winner.rmse, mape=selection.winner.mape,
                        r2=selection.winner.r2, best_baseline_mae=best_baseline_mae,
                        beats_baseline=selection.winner.mae < best_baseline_mae,
                    )

                artifact_path = artifacts_dir / f"{model_version}.pkl"
                with artifact_path.open("wb") as f:
                    pickle.dump(model, f)

                training_meta = TrainingMetadata(
                    algorithm=selection.winner.algorithm,
                    dataset_fingerprint=dataset_fingerprint,
                    training_data_start_period_ordinal=current_min_period_ordinal,
                    training_data_end_period_ordinal=current_max_period_ordinal,
                    training_record_count=training_record_count,
                )

                model_registry_key = record_candidate(
                    engine, program_key, metric, model_version, candidate, training_meta,
                    str(artifact_path), decision, college_key=college_key,
                )

                result_kwargs = dict(
                    program_id=program_id,
                    program_key=program_key,
                    college_id=college_id,
                    college_key=college_key,
                    metric=metric,
                    retrained=True,
                    model_version=model_version,
                    promoted=decision.promote,
                    reason=decision.reason,
                    candidate_mae=candidate.mae,
                    algorithm=selection.winner.algorithm,
                )

                if not decision.promote:
                    logger.warning("Candidate %s NOT promoted: %s", model_version, decision.reason)
                    results.append(DeploymentResult(**result_kwargs))
                    continue

                yhat, yhat_lower, yhat_upper = _forecast_next_period(model, target_ds)

                _write_forecast_row(
                    engine,
                    program_key=program_key,
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
                    "Promoted %s (algorithm=%s, MAE %.4f): forecast %.2f for %s-%s",
                    model_version, selection.winner.algorithm, candidate.mae, yhat, target_year, target_semester,
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
    from pipelines.common.settings import get_postgres_settings
    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    logging.basicConfig(level=logging.INFO)
    password = get_postgres_settings().require_pipeline_writer_password()
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
    # Option B / P1.5 (graduation_count reporting honesty): coverage and
    # champion-algorithm mix are the headline here -- MAPE never is. A
    # per-metric breakdown because a mixed enrollment_count+graduation_count
    # aggregate hides exactly the kind of series-level story this section
    # exists to surface (docs/10_Forecasting.md P1.5/P1.6).
    for metric in TARGET_METRICS:
        metric_outcomes = [r for r in outcomes if r.metric == metric]
        metric_promoted = [r for r in metric_outcomes if r.promoted]
        pct = f"{len(metric_promoted) / len(metric_outcomes):.0%}" if metric_outcomes else "N/A"
        print(f"  {metric}: {len(metric_promoted)}/{len(metric_outcomes)} series have a deployed champion ({pct})")
        algo_counts: dict[str, int] = {}
        for r in metric_promoted:
            algo_counts[r.algorithm or "unknown"] = algo_counts.get(r.algorithm or "unknown", 0) + 1
        if algo_counts:
            breakdown = ", ".join(f"{algo}: {n}" for algo, n in sorted(algo_counts.items()))
            print(f"    champion algorithm mix -- {breakdown}")
    for r in skipped:
        print(f"  SKIPPED {r.program_id} ({r.college_id})/{r.metric}: {r.reason}")
    for r in rejected:
        print(f"  REJECTED {r.program_id} ({r.college_id})/{r.metric}: {r.reason}")