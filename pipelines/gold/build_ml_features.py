"""
pipelines/gold/build_ml_features.py

Builds gold.ml_forecast_features: one row per (college, semester), with
lag/rolling/historical-average/trend/seasonality features for each
forecast target metric (enrollment_count, graduation_count), per
docs/10_Forecasting.md's feature design.

THE CENTRAL DESIGN CONSTRAINT, enforced by construction, not by
afterward-checking: every feature describing semester t uses ONLY data
from semesters strictly before t. Postgres window frames with
`ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` (or `2 PRECEDING AND
1 PRECEDING` for a 2-period rolling average) enforce this at the SQL
level: the current row is structurally excluded from its own feature
computation, not just conventionally excluded by a naming decision.

Two adaptations from docs/10_Forecasting.md's original (more abstract)
feature list, made concrete and disclosed here:

1. SCOPE: features are built at COLLEGE grain only, sourced from
   gold.fact_institution_kpi (Day 14). Program-grain forecasting is not
   built -- fact_institution_kpi's Success Rate components are
   explicitly college-level (docs/09_Data_Science.md).

2. SEASONALITY: the doc's original formula ("deviation from a fitted
   trend line, same-semester-parity values") is simplified here to "the
   historical average of this metric at this same semester-of-year
   position (1 or 2), using only strictly prior years' occurrences" --
   a plain window-function AVG, not a joint trend-fit-then-deviate
   computation.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from pipelines.common.postgres import replace_table_contents

TARGET_METRICS = ["enrollment_count", "graduation_count"]


def _metric_feature_sql(metric: str) -> str:
    """Build the SELECT fragment for one target metric's full feature
    set. `metric` must already be a trusted column name (only ever
    called with TARGET_METRICS, never user input).

    Every column reference here is qualified with the `kpi.` alias.
    Without that, `semester_key` (present on both fact_institution_kpi
    and dim_semester once the outer query joins them) becomes an
    ambiguous column reference -- caught by actually running the query,
    not by reasoning about the join in advance.
    """
    return f"""
        kpi.{metric} AS {metric},
        LAG(kpi.{metric}, 1) OVER w AS {metric}_lag_1,
        LAG(kpi.{metric}, 2) OVER w AS {metric}_lag_2,
        AVG(kpi.{metric}) OVER (
            PARTITION BY kpi.college_key ORDER BY kpi.semester_key
            ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING
        ) AS {metric}_rolling_avg_2,
        AVG(kpi.{metric}) OVER (
            PARTITION BY kpi.college_key ORDER BY kpi.semester_key
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_historical_avg,
        REGR_SLOPE(kpi.{metric}, kpi.semester_key) OVER (
            PARTITION BY kpi.college_key ORDER BY kpi.semester_key
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_trend,
        AVG(kpi.{metric}) OVER (
            PARTITION BY kpi.college_key, sem.semester_number ORDER BY kpi.semester_key
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS {metric}_seasonality,
        (
            LAG(kpi.{metric}, 1) OVER w - LAG(kpi.{metric}, 2) OVER w
        )::float / NULLIF(LAG(kpi.{metric}, 2) OVER w, 0) AS {metric}_growth
    """


def build_ml_features_sql(target_metrics: Optional[List[str]] = None) -> str:
    """Build the full feature-engineering SQL query against
    gold.fact_institution_kpi, joined to gold.dim_semester for
    academic_year/semester_number -- these are dimension attributes, not
    columns on the fact table itself (a schema assumption that was wrong
    on the first attempt at this query, caught immediately by actually
    running it: Postgres rejected `academic_year` as an undefined column
    the moment this was executed against the real warehouse, rather than
    silently returning something plausible-looking).
    """
    target_metrics = target_metrics or TARGET_METRICS
    metric_blocks = ",\n        ".join(_metric_feature_sql(m) for m in target_metrics)

    return f"""
        SELECT
            kpi.college_key,
            kpi.semester_key,
            sem.academic_year,
            sem.semester_number,
            {metric_blocks}
        FROM gold.fact_institution_kpi kpi
        JOIN gold.dim_semester sem ON kpi.semester_key = sem.semester_key
        WINDOW w AS (PARTITION BY kpi.college_key ORDER BY kpi.semester_key)
        ORDER BY kpi.college_key, kpi.semester_key
    """


def build_ml_forecast_features(engine, target_metrics: Optional[List[str]] = None) -> pd.DataFrame:
    """Execute the feature SQL against the live warehouse and return the
    resulting DataFrame -- one row per (college, semester)."""
    sql = build_ml_features_sql(target_metrics)
    return pd.read_sql(sql, engine)


def build_and_store_ml_features(engine, target_metrics: Optional[List[str]] = None) -> int:
    """Build ml_forecast_features and write it to gold.ml_forecast_features
    (TRUNCATE-safe, so it survives any future dbt views built on top of it)."""
    df = build_ml_forecast_features(engine, target_metrics)
    replace_table_contents(engine, "gold", "ml_forecast_features", df)
    return len(df)


if __name__ == "__main__":
    import os

    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    password = os.environ["PIPELINE_WRITER_PASSWORD"]
    engine = build_pipeline_writer_engine(password)
    row_count = build_and_store_ml_features(engine)
    print(f"gold.ml_forecast_features built: {row_count} rows")
