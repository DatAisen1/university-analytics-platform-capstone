"""
pipelines/gold/build_ml_features.py

Builds two Gold ML-feature tables from fact_enrollment / fact_graduation
directly (NOT from fact_institution_kpi, which is college-only grain by
deliberate design -- see build_kpi.py's module docstring):

  gold.ml_program_forecast_features
      Grain: (college_key, program_key, academic_period_key).
      Both target metrics (enrollment_count, graduation_count), because
      this is the finest grain the two source facts share.

  gold.ml_enrollment_features_by_year_level
      Grain: (college_key, program_key, year_level_key, academic_period_key).
      enrollment_count ONLY -- fact_graduation carries no year_level_key,
      so a shared (..., year_level, ...) grain across both metrics isn't
      something the data actually supports. Fabricating one would be
      worse than not having it (Task 31: respect the actual grain, don't
      pretend a coarser fact is finer than it is).

THE CENTRAL LEAKAGE-PREVENTION CONSTRAINT (Task 32), enforced by
construction: every feature describing period t uses ONLY periods
strictly before t, via `ROWS BETWEEN ... AND 1 PRECEDING` window frames
that structurally exclude the current row -- not a convention that could
be silently violated by column ordering.

ORDERING FIX vs. the previous version: windows are partitioned/ordered
by dim_academic_period.period_ordinal, not by the academic_period_key
surrogate. This mirrors build_kpi.py's compute_program_completion_momentum,
which already had to make this exact fix ("matched via period_ordinal
(chronological), not surrogate-key arithmetic"). Ordering by a surrogate
key that isn't guaranteed chronological would silently let a "prior
periods" window frame include a period that is chronologically later --
a genuine data-leakage bug, not just a style issue.

REPRODUCIBILITY (Task 33): every query is pure SQL with no RANDOM(),
no unordered aggregation, and a full ORDER BY on the grain columns in
the final result -- given the same rows in fact_enrollment /
fact_graduation / dim_academic_period, the output DataFrame is
byte-for-byte identical across runs. `feature_dataset_fingerprint()`
makes that property directly checkable rather than assumed (see
tests/unit/test_build_ml_features.py).
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

import pandas as pd

from pipelines.common.postgres import replace_table_contents
from pipelines.common.errors import FeatureEngineeringError
PROGRAM_GRAIN_METRICS = ["enrollment_count", "graduation_count"]
YEAR_LEVEL_GRAIN_METRICS = ["enrollment_count"]


def _lag_rolling_trend_sql(metric: str, partition_cols: str) -> str:
    """One metric's full lag/rolling/trend/seasonality/growth feature
    set, windowed over `partition_cols` (a pre-built SQL fragment, e.g.
    "college_key, program_key"), ordered by period_ordinal -- the
    chronological key, not the academic_period_key surrogate.

    Every window frame ends at `1 PRECEDING`: the current row is
    structurally excluded from its own feature computation (Task 32).
    """
    return f"""
        {metric} AS {metric},
        LAG({metric}, 1) OVER w_{metric} AS {metric}_lag_1,
        LAG({metric}, 2) OVER w_{metric} AS {metric}_lag_2,
        AVG({metric}) OVER (
            PARTITION BY {partition_cols} ORDER BY period_ordinal
            ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING
        ) AS {metric}_rolling_avg_2,
        AVG({metric}) OVER (
            PARTITION BY {partition_cols} ORDER BY period_ordinal
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_historical_avg,
        REGR_SLOPE({metric}, period_ordinal) OVER (
            PARTITION BY {partition_cols} ORDER BY period_ordinal
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_trend,
        AVG({metric}) OVER (
            PARTITION BY {partition_cols}, semester_number ORDER BY period_ordinal
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_seasonality,
        (
            LAG({metric}, 1) OVER w_{metric} - LAG({metric}, 2) OVER w_{metric}
        )::float / NULLIF(LAG({metric}, 2) OVER w_{metric}, 0) AS {metric}_growth
    """


def build_program_forecast_features_sql() -> str:
    """Grain: (college_key, program_key, academic_period_key). Both
    enrollment_count and graduation_count, aggregated directly from the
    two source facts -- deliberately bypassing fact_institution_kpi,
    which is college-only grain and would collapse every program into
    one row per college (exactly the "accidentally aggregate all
    students together" failure mode Task 31 calls out).
    """
    partition_cols = "college_key, program_key"
    metric_blocks = ",\n        ".join(
        _lag_rolling_trend_sql(m, partition_cols) for m in PROGRAM_GRAIN_METRICS
    )
    window_defs = ",\n        ".join(
        f"w_{m} AS (PARTITION BY {partition_cols} ORDER BY period_ordinal)"
        for m in PROGRAM_GRAIN_METRICS
    )

    return f"""
        WITH enrollment_agg AS (
            SELECT college_key, program_key, academic_period_key,
                   COUNT(*) AS enrollment_count
            FROM gold.fact_enrollment
            GROUP BY college_key, program_key, academic_period_key
        ),
        graduation_agg AS (
            SELECT college_key, program_key, academic_period_key,
                   COUNT(*) AS graduation_count
            FROM gold.fact_graduation
            GROUP BY college_key, program_key, academic_period_key
        ),
        -- FULL OUTER JOIN: a program can have enrollments with zero
        -- graduations that period (normal) or vice versa (a program in
        -- teach-out). Either side missing must become 0, never a
        -- dropped row -- a dropped row would silently corrupt every
        -- later window function's chronological spacing.
        combined AS (
            SELECT
                COALESCE(e.college_key, g.college_key) AS college_key,
                COALESCE(e.program_key, g.program_key) AS program_key,
                COALESCE(e.academic_period_key, g.academic_period_key) AS academic_period_key,
                COALESCE(e.enrollment_count, 0) AS enrollment_count,
                COALESCE(g.graduation_count, 0) AS graduation_count
            FROM enrollment_agg e
            FULL OUTER JOIN graduation_agg g
                ON e.college_key = g.college_key
                AND e.program_key = g.program_key
                AND e.academic_period_key = g.academic_period_key
        ),
        with_period AS (
            SELECT
                c.college_key, c.program_key, c.academic_period_key,
                ap.academic_year, ap.semester_number, ap.period_ordinal,
                c.enrollment_count, c.graduation_count
            FROM combined c
            JOIN gold.dim_academic_period ap ON c.academic_period_key = ap.academic_period_key
        )
        SELECT
            college_key, program_key, academic_period_key,
            academic_year, semester_number, period_ordinal,
            {metric_blocks}
        FROM with_period
        WINDOW {window_defs}
        ORDER BY college_key, program_key, period_ordinal
    """


def build_enrollment_features_by_year_level_sql() -> str:
    """Grain: (college_key, program_key, year_level_key, academic_period_key).
    Enrollment-only -- see module docstring for why graduation can't
    share this grain.
    """
    partition_cols = "college_key, program_key, year_level_key"
    metric_blocks = ",\n        ".join(
        _lag_rolling_trend_sql(m, partition_cols) for m in YEAR_LEVEL_GRAIN_METRICS
    )
    window_defs = ",\n        ".join(
        f"w_{m} AS (PARTITION BY {partition_cols} ORDER BY period_ordinal)"
        for m in YEAR_LEVEL_GRAIN_METRICS
    )

    return f"""
        WITH enrollment_agg AS (
            SELECT college_key, program_key, year_level_key, academic_period_key,
                   COUNT(*) AS enrollment_count
            FROM gold.fact_enrollment
            GROUP BY college_key, program_key, year_level_key, academic_period_key
        ),
        with_period AS (
            SELECT
                e.college_key, e.program_key, e.year_level_key, e.academic_period_key,
                ap.academic_year, ap.semester_number, ap.period_ordinal,
                e.enrollment_count
            FROM enrollment_agg e
            JOIN gold.dim_academic_period ap ON e.academic_period_key = ap.academic_period_key
        )
        SELECT
            college_key, program_key, year_level_key, academic_period_key,
            academic_year, semester_number, period_ordinal,
            {metric_blocks}
        FROM with_period
        WINDOW {window_defs}
        ORDER BY college_key, program_key, year_level_key, period_ordinal
    """


def feature_dataset_fingerprint(df: pd.DataFrame) -> str:
    """A stable SHA-256 hash of a feature DataFrame's content, used to
    verify Task 33's reproducibility property: same input rows -> same
    fingerprint, every run. Sorting by every column before hashing means
    the fingerprint is insensitive to row order the database happened
    to return them in (Postgres doesn't guarantee physical row order
    without ORDER BY, even though our queries always specify one) --
    belt-and-suspenders on top of the query's own ORDER BY.
    """
    stable = df.sort_values(by=list(df.columns)).reset_index(drop=True)
    payload = stable.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_program_forecast_features(engine) -> pd.DataFrame:
    return pd.read_sql(build_program_forecast_features_sql(), engine)


def build_enrollment_features_by_year_level(engine) -> pd.DataFrame:
    return pd.read_sql(build_enrollment_features_by_year_level_sql(), engine)


def build_and_store_ml_features(engine) -> dict:
    try:
        program_df = build_program_forecast_features(engine)
        year_level_df = build_enrollment_features_by_year_level(engine)
    except Exception as exc:  # SQL/window-function failures against the warehouse
        raise FeatureEngineeringError(
            f"Failed to build ML forecast feature tables: {exc}",
            stage="Feature Engineering",
        ) from exc

    if program_df.empty or year_level_df.empty:
        raise FeatureEngineeringError(
            "ML feature build produced an empty feature table -- check that gold.fact_enrollment / "
            "gold.fact_graduation have data for the requested period.",
            stage="Feature Engineering", rows_affected=0,
            details={"program_rows": len(program_df), "year_level_rows": len(year_level_df)},
        )

    replace_table_contents(engine, "gold", "ml_program_forecast_features", program_df)
    replace_table_contents(engine, "gold", "ml_enrollment_features_by_year_level", year_level_df)

    return {
        "ml_program_forecast_features_rows": len(program_df),
        "ml_enrollment_features_by_year_level_rows": len(year_level_df),
        "ml_program_forecast_features_fingerprint": feature_dataset_fingerprint(program_df),
        "ml_enrollment_features_by_year_level_fingerprint": feature_dataset_fingerprint(year_level_df),
    }


if __name__ == "__main__":
    from pipelines.common.settings import get_postgres_settings
    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)
    summary = build_and_store_ml_features(engine)
    for key, value in summary.items():
        print(f"{key}: {value}")