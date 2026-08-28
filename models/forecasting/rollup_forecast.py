"""
models/forecasting/rollup_forecast.py

DS Evaluation task P1.1/P1.2: college-level and campus-wide forecast
outcomes, built as bottom-up aggregations of the already-trained,
already-promoted program-level champion forecasts in gold.fact_forecast
(forecast_grain='program') -- NOT a second/third independent model
family. This is standard hierarchical forecast reconciliation (see e.g.
Hyndman & Athanasopoulos, "Forecasting: Principles and Practice", ch. 11):
a department's forecast is its programs' forecasts summed; campus-wide is
every department summed. Cheaper than training separate models per
level, and self-consistent by construction -- the parts always add up to
the whole.

WHICH program-grain rows get summed, and why this isn't just
`SELECT * FROM fact_forecast WHERE forecast_grain='program'`:
model_version is timestamped per retrain (make_model_version), and a
promoted model's forecast rows are never deleted on a later retrain --
so fact_forecast can (and after more than one deploy_forecasts() run,
does) hold MULTIPLE historical rows for the same (program_key, metric)
at different target_period_ordinal values, some from models that are no
longer the champion. Rolling up "whatever rows happen to be in the
table" would double-count retired models' stale forecasts. The correct
input is: the CURRENT CHAMPION's most recent forecast per
(program_key, metric) -- i.e. join to model_registry.is_champion = TRUE,
then take the max target_period_ordinal in case a long-lived champion
has forecast rows for more than one past target period.

WHY prediction intervals are summed directly (yhat_lower/yhat_upper),
not variance-combined: the statistically "correct" way to combine
independent forecast uncertainties is sqrt(sum of variances), which
produces a NARROWER interval than a naive linear sum -- summing
directly is a deliberately conservative (wider) approximation, not a
rigorous propagation. This is disclosed rather than silently assumed
correct: doing this properly would require each program's actual
forecast variance (not just its 80% CI bounds) and a characterization of
cross-program error correlation (enrollment shocks plausibly affect
multiple programs together, which would make a naive linear sum closer
to correct than the independence-assuming sqrt approach anyway) --
neither of which this project has done the work to establish. Flagged
as a real follow-up in docs/10_Forecasting.md, not fixed here.

model_version for rollup rows is a STABLE, non-timestamped label
("bottom_up_v1") rather than make_model_version's timestamped scheme --
rollups aren't a trained artifact with a training timestamp, and a
stable version string is what makes re-running this idempotent (the
partial-unique-index ON CONFLICT upserts the same row rather than
accumulating duplicates every run). The "_v1" suffix exists so a future
change to the AGGREGATION METHOD (e.g. switching to variance-combined
intervals) can bump to "_v2" without silently colliding with rows
produced by the old method.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

import pandas as pd

from pipelines.common.errors import ForecastError

logger = logging.getLogger(__name__)

ROLLUP_MODEL_VERSION = "bottom_up_v1"

_CHAMPION_PROGRAM_FORECASTS_SQL = """
    SELECT DISTINCT ON (f.program_key, f.metric)
        f.program_key, f.college_key, f.metric,
        f.target_academic_year, f.target_semester_number, f.target_period_ordinal,
        f.yhat, f.yhat_lower, f.yhat_upper
    FROM gold.fact_forecast f
    JOIN gold.model_registry mr ON mr.model_registry_key = f.model_registry_key
    WHERE f.forecast_grain = 'program' AND mr.is_champion IS TRUE
    ORDER BY f.program_key, f.metric, f.target_period_ordinal DESC
"""


@dataclass(frozen=True)
class RollupResult:
    grain: str  # 'college' or 'campus'
    metric: str
    college_key: int | None  # None for campus grain
    target_period_ordinal: int
    program_count: int  # how many program-level forecasts fed this row -- coverage signal
    yhat: float


_TOTAL_PROGRAMS_PER_COLLEGE_SQL = """
    SELECT college_key, count(*) AS total_programs
    FROM gold.dim_program
    GROUP BY college_key
"""


def _load_champion_program_forecasts(engine) -> pd.DataFrame:
    return pd.read_sql(_CHAMPION_PROGRAM_FORECASTS_SQL, engine)


def _load_total_programs_per_college(engine) -> pd.DataFrame:
    """Denominator for the coverage signal -- ALL programs in
    dim_program, not just the ones that happen to have a champion. This
    is what makes covered_entity_count/total_entity_count meaningful:
    without the true denominator, "5 programs covered" tells a consumer
    nothing about whether that's 5-of-5 or 5-of-37."""
    return pd.read_sql(_TOTAL_PROGRAMS_PER_COLLEGE_SQL, engine)


def _write_rollup_row(
    engine,
    forecast_grain: str,
    college_key: int | None,
    metric: str,
    target_academic_year: int,
    target_semester_number: int,
    target_period_ordinal: int,
    yhat: float,
    yhat_lower: float,
    yhat_upper: float,
    covered_entity_count: int,
    total_entity_count: int,
) -> None:
    """Two distinct ON CONFLICT targets, one per grain's partial unique
    index (migration 0016) -- college_key is part of the college-grain
    index but doesn't exist in the campus-grain index (there's only ever
    one campus-wide row per metric/period), so these cannot share a
    single parameterized query."""
    conn = engine.raw_connection()
    try:
        with conn.cursor() as cur:
            if forecast_grain == "college":
                cur.execute(
                    """
                    INSERT INTO gold.fact_forecast (
                        college_key, metric, target_academic_year, target_semester_number,
                        target_period_ordinal, model_version, yhat, yhat_lower, yhat_upper,
                        forecast_grain, covered_entity_count, total_entity_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'college', %s, %s)
                    ON CONFLICT (college_key, metric, target_period_ordinal, model_version)
                        WHERE forecast_grain = 'college'
                    DO UPDATE SET
                        yhat = EXCLUDED.yhat,
                        yhat_lower = EXCLUDED.yhat_lower,
                        yhat_upper = EXCLUDED.yhat_upper,
                        covered_entity_count = EXCLUDED.covered_entity_count,
                        total_entity_count = EXCLUDED.total_entity_count,
                        generated_at = now()
                    """,
                    (
                        college_key, metric, target_academic_year, target_semester_number,
                        target_period_ordinal, ROLLUP_MODEL_VERSION, yhat, yhat_lower, yhat_upper,
                        covered_entity_count, total_entity_count,
                    ),
                )
            elif forecast_grain == "campus":
                cur.execute(
                    """
                    INSERT INTO gold.fact_forecast (
                        metric, target_academic_year, target_semester_number,
                        target_period_ordinal, model_version, yhat, yhat_lower, yhat_upper,
                        forecast_grain, covered_entity_count, total_entity_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'campus', %s, %s)
                    ON CONFLICT (metric, target_period_ordinal, model_version)
                        WHERE forecast_grain = 'campus'
                    DO UPDATE SET
                        yhat = EXCLUDED.yhat,
                        yhat_lower = EXCLUDED.yhat_lower,
                        yhat_upper = EXCLUDED.yhat_upper,
                        covered_entity_count = EXCLUDED.covered_entity_count,
                        total_entity_count = EXCLUDED.total_entity_count,
                        generated_at = now()
                    """,
                    (
                        metric, target_academic_year, target_semester_number,
                        target_period_ordinal, ROLLUP_MODEL_VERSION, yhat, yhat_lower, yhat_upper,
                        covered_entity_count, total_entity_count,
                    ),
                )
            else:
                raise ValueError(f"forecast_grain must be 'college' or 'campus', got {forecast_grain!r}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_rollups(engine) -> List[RollupResult]:
    """Reads every current champion's program-level forecast, aggregates
    to college and campus-wide, writes both grains to gold.fact_forecast,
    and returns a per-row summary (including program_count, a direct
    coverage signal -- e.g. a college rollup built from only 2 of its 6
    programs is real but should be read differently than one built from
    all 6, which is exactly why program_count is returned rather than
    hidden)."""
    try:
        program_forecasts = _load_champion_program_forecasts(engine)
        total_programs_per_college = _load_total_programs_per_college(engine)
        total_programs_overall = int(total_programs_per_college["total_programs"].sum())
        results: List[RollupResult] = []

        if program_forecasts.empty:
            logger.warning("No champion program-level forecasts found -- nothing to roll up.")
            return results

        # College grain: sum programs within the same (college, metric,
        # target_period_ordinal). Grouping by target_period_ordinal (not
        # assuming every program shares the same one) means a college
        # whose programs have differing history lengths still gets a
        # correct sum PER target period, never mixing forecasts for
        # different future semesters into one number.
        college_groups = program_forecasts.groupby(
            ["college_key", "metric", "target_academic_year", "target_semester_number", "target_period_ordinal"]
        ).agg(
            yhat=("yhat", "sum"),
            yhat_lower=("yhat_lower", "sum"),
            yhat_upper=("yhat_upper", "sum"),
            program_count=("program_key", "nunique"),
        ).reset_index()
        college_groups = college_groups.merge(total_programs_per_college, on="college_key", how="left")

        for _, row in college_groups.iterrows():
            _write_rollup_row(
                engine,
                forecast_grain="college",
                college_key=int(row["college_key"]),
                metric=row["metric"],
                target_academic_year=int(row["target_academic_year"]),
                target_semester_number=int(row["target_semester_number"]),
                target_period_ordinal=int(row["target_period_ordinal"]),
                yhat=float(row["yhat"]),
                yhat_lower=float(row["yhat_lower"]),
                yhat_upper=float(row["yhat_upper"]),
                covered_entity_count=int(row["program_count"]),
                total_entity_count=int(row["total_programs"]),
            )
            results.append(RollupResult(
                grain="college", metric=row["metric"], college_key=int(row["college_key"]),
                target_period_ordinal=int(row["target_period_ordinal"]),
                program_count=int(row["program_count"]), yhat=float(row["yhat"]),
            ))
        logger.info("Wrote %d college-grain rollup rows.", len(college_groups))

        # Campus-wide grain: sum ACROSS colleges too, same (metric,
        # target_period_ordinal) grouping, built from the SAME
        # program-level input (not from the college rollup just written)
        # -- avoids two slightly-inconsistent summation paths, per the
        # backlog item this satisfies.
        campus_groups = program_forecasts.groupby(
            ["metric", "target_academic_year", "target_semester_number", "target_period_ordinal"]
        ).agg(
            yhat=("yhat", "sum"),
            yhat_lower=("yhat_lower", "sum"),
            yhat_upper=("yhat_upper", "sum"),
            program_count=("program_key", "nunique"),
        ).reset_index()

        for _, row in campus_groups.iterrows():
            _write_rollup_row(
                engine,
                forecast_grain="campus",
                college_key=None,
                metric=row["metric"],
                target_academic_year=int(row["target_academic_year"]),
                target_semester_number=int(row["target_semester_number"]),
                target_period_ordinal=int(row["target_period_ordinal"]),
                yhat=float(row["yhat"]),
                yhat_lower=float(row["yhat_lower"]),
                yhat_upper=float(row["yhat_upper"]),
                covered_entity_count=int(row["program_count"]),
                total_entity_count=total_programs_overall,
            )
            results.append(RollupResult(
                grain="campus", metric=row["metric"], college_key=None,
                target_period_ordinal=int(row["target_period_ordinal"]),
                program_count=int(row["program_count"]), yhat=float(row["yhat"]),
            ))
        logger.info("Wrote %d campus-wide rollup rows.", len(campus_groups))

        return results
    except Exception as exc:
        raise ForecastError(f"Forecast rollup failed: {exc}", stage="Forecast Rollup") from exc


if __name__ == "__main__":
    from pipelines.common.settings import get_postgres_settings
    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    logging.basicConfig(level=logging.INFO)
    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)

    print("Building college and campus-wide forecast rollups...")
    outcomes = build_rollups(engine)
    for r in outcomes:
        scope = f"college_key={r.college_key}" if r.grain == "college" else "CAMPUS-WIDE"
        print(f"  {r.grain:8s} {r.metric:18s} {scope}: yhat={r.yhat:.1f} (from {r.program_count} program(s))")