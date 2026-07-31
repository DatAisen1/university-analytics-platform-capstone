"""
pipelines/gold/build_kpi.py

Computes fact_institution_kpi: the weighted Success Rate composite from
docs/09_Data_Science.md, one row per (college, semester), plus its six
component sub-metrics (stored alongside the composite, per that doc's
transparency principle -- the number is never shown without its inputs).

Two components required a concrete derivation choice the design doc left
abstract, made explicit here rather than silently decided:

  - Graduation Rate's denominator ("students eligible to graduate") uses
    year_level >= ceil(nominal_duration_years) as an eligibility proxy,
    since exact semester-tenure isn't a column on fact_enrollment. This
    is a slight UNDER-estimate of true eligibility (year_level lags
    tenure whenever a student has stalled, never leads it), which is a
    conservative, disclosed approximation, not an attempt to inflate the
    rate.
  - Program Completion Momentum compares each student's year_level this
    semester against their OWN year_level last semester (a self-join on
    fact_enrollment, the same pattern fact_retention already uses) --
    students with no prior-semester record (new entrants) are excluded
    from both numerator and denominator, since "did they advance" isn't
    a meaningful question for someone in their first semester.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import duckdb
import pandas as pd

from pipelines.common.config import ConfigError
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage, ObjectStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

STAGE = "gold_build_kpi"

# Weights from docs/09_Data_Science.md Section 3 -- must sum to 1.0.
WEIGHTS = {
    "graduation_rate": 0.30,
    "retention_rate": 0.25,
    "dropout_rate": 0.20,      # applied to (1 - dropout_rate)
    "program_completion_momentum": 0.15,
    "shifter_stability": 0.05,
    "enrollment_stability": 0.05,
}


def _validate_weights() -> None:
    total = sum(WEIGHTS.values())
    if abs(total - 1.0) > 0.0001:
        raise ConfigError(f"Success Rate weights must sum to 1.0, got {total}")


def compute_success_rate(
    retention_rate: float,
    graduation_rate: float,
    dropout_rate: float,
    shifter_stability: float,
    enrollment_stability: float,
    program_completion_momentum: float,
) -> float:
    """The weighted composite formula from docs/09_Data_Science.md Section 3,
    scaled to a 0-100 index. Pure function, independently testable against
    the doc's own worked example (Section 4)."""
    _validate_weights()
    score = (
        WEIGHTS["graduation_rate"] * graduation_rate
        + WEIGHTS["retention_rate"] * retention_rate
        + WEIGHTS["dropout_rate"] * (1 - dropout_rate)
        + WEIGHTS["program_completion_momentum"] * program_completion_momentum
        + WEIGHTS["shifter_stability"] * shifter_stability
        + WEIGHTS["enrollment_stability"] * enrollment_stability
    )
    return round(score * 100, 1)


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def compute_program_completion_momentum(
    fact_enrollment: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """Per (college_key, semester_key): fraction of continuing students
    (those with a record in BOTH this semester and the immediately prior
    one) whose year_level increased. Returns one row per
    (college_key, semester_key) with a `momentum` column."""
    conn.register("fe", fact_enrollment)
    result = conn.execute(
        """
        SELECT
            curr.college_key,
            curr.semester_key,
            AVG(CASE WHEN curr.year_level > prev.year_level THEN 1.0 ELSE 0.0 END) AS momentum
        FROM fe curr
        JOIN fe prev
            ON curr.student_key = prev.student_key
            AND curr.semester_key = prev.semester_key + 1
        GROUP BY curr.college_key, curr.semester_key
        """
    ).df()
    conn.unregister("fe")
    return result


def build_fact_institution_kpi(
    fact_enrollment: pd.DataFrame,
    fact_graduation: pd.DataFrame,
    fact_dropout: pd.DataFrame,
    fact_shifter: pd.DataFrame,
    fact_retention: pd.DataFrame,
    dim_program: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """Aggregate all five base facts into one row per (college_key,
    semester_key) with all six Success Rate components plus the
    composite score.
    """
    nominal_duration_by_program_key = dict(
        zip(dim_program["program_key"], dim_program["nominal_duration_years"])
    )
    fact_enrollment = fact_enrollment.copy()
    fact_enrollment["nominal_duration_years"] = fact_enrollment["program_key"].map(nominal_duration_by_program_key)
    fact_enrollment["is_eligible_to_graduate"] = (
        fact_enrollment["year_level"] >= fact_enrollment["nominal_duration_years"].apply(
            lambda x: int(x) if x == int(x) else int(x) + 1
        )
    )

    # fact_shifter has no college_key of its own -- a shift event spans TWO
    # programs (from/to), possibly two different colleges, so there's no
    # single unambiguous college_key on the fact row itself. For KPI
    # attribution, a shift is counted against the FROM college: it's that
    # college's population being depleted, which is what
    # shifter_stability is meant to measure.
    program_college_by_key = dict(zip(dim_program["program_key"], dim_program["college_key"]))
    fact_shifter = fact_shifter.copy()
    fact_shifter["college_key"] = fact_shifter["from_program_key"].map(program_college_by_key)

    enrollment_counts = fact_enrollment.groupby(["college_key", "semester_key"]).size().rename("enrollment_count")
    eligible_counts = fact_enrollment[fact_enrollment["is_eligible_to_graduate"]].groupby(
        ["college_key", "semester_key"]
    ).size().rename("eligible_count")
    graduation_counts = fact_graduation.groupby(["college_key", "semester_key"]).size().rename("graduation_count")
    dropout_counts = fact_dropout.groupby(["college_key", "semester_key"]).size().rename("dropout_count")
    shifter_counts = fact_shifter.groupby(["college_key", "semester_key"]).size().rename("shifter_count")
    retention_rates = fact_retention.groupby(["college_key", "semester_key"])["is_retained"].mean().rename("retention_rate")
    momentum_df = compute_program_completion_momentum(fact_enrollment, conn).set_index(["college_key", "semester_key"])["momentum"]

    kpi = pd.concat(
        [enrollment_counts, eligible_counts, graduation_counts, dropout_counts,
         shifter_counts, retention_rates, momentum_df],
        axis=1,
    ).reset_index()

    kpi["eligible_count"] = kpi["eligible_count"].fillna(0)
    kpi["graduation_count"] = kpi["graduation_count"].fillna(0)
    kpi["dropout_count"] = kpi["dropout_count"].fillna(0)
    kpi["shifter_count"] = kpi["shifter_count"].fillna(0)
    kpi["retention_rate"] = kpi["retention_rate"].fillna(0.0)
    kpi["momentum"] = kpi["momentum"].fillna(0.0)  # first semester colleges have no prior-semester students

    kpi["graduation_rate"] = (kpi["graduation_count"] / kpi["eligible_count"].replace(0, pd.NA)).fillna(0.0).astype(float)
    kpi["dropout_rate"] = kpi["dropout_count"] / kpi["enrollment_count"]
    kpi["shifter_stability"] = 1 - (kpi["shifter_count"] / kpi["enrollment_count"])

    kpi = kpi.sort_values(["college_key", "semester_key"]).reset_index(drop=True)
    prior_enrollment = kpi.groupby("college_key")["enrollment_count"].shift(1)
    with pd.option_context("mode.chained_assignment", None):
        stability = 1 - (kpi["enrollment_count"] - prior_enrollment).abs() / prior_enrollment
    kpi["enrollment_stability"] = stability.clip(lower=0.0, upper=1.0).fillna(1.0)  # no prior semester -> neutral default

    kpi["success_rate"] = kpi.apply(
        lambda row: compute_success_rate(
            retention_rate=row["retention_rate"],
            graduation_rate=row["graduation_rate"],
            dropout_rate=row["dropout_rate"],
            shifter_stability=row["shifter_stability"],
            enrollment_stability=row["enrollment_stability"],
            program_completion_momentum=row["momentum"],
        ),
        axis=1,
    )

    return kpi[[
        "college_key", "semester_key", "enrollment_count", "graduation_count", "dropout_count",
        "shifter_count", "retention_rate", "graduation_rate", "dropout_rate", "shifter_stability",
        "enrollment_stability", "momentum", "success_rate",
    ]].rename(columns={"momentum": "program_completion_momentum"})


def build_kpi(
    gold_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, object]:
    _validate_weights()
    gold_storage = gold_storage or LocalFileStorage(DEFAULT_GOLD_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    fact_enrollment = _read_parquet(gold_storage, "gold/fact_enrollment/data.parquet")
    fact_graduation = _read_parquet(gold_storage, "gold/fact_graduation/data.parquet")
    fact_dropout = _read_parquet(gold_storage, "gold/fact_dropout/data.parquet")
    fact_shifter = _read_parquet(gold_storage, "gold/fact_shifter/data.parquet")
    fact_retention = _read_parquet(gold_storage, "gold/fact_retention/data.parquet")
    dim_program = _read_parquet(gold_storage, "gold/dim_program/data.parquet")

    conn = duckdb.connect(":memory:")
    kpi_df = build_fact_institution_kpi(
        fact_enrollment, fact_graduation, fact_dropout, fact_shifter, fact_retention, dim_program, conn
    )
    conn.close()

    _write_parquet(gold_storage, "gold/fact_institution_kpi/data.parquet", kpi_df)

    record_run(
        meta_conn, str(uuid.uuid4()), batch_id=str(uuid.uuid4()), stage=STAGE, entity="fact_institution_kpi",
        partition_key="all", started_at=datetime.now(timezone.utc), status="SUCCESS",
        rows_out=len(kpi_df), source_path="gold/fact_*",
    )
    if owns_conn:
        meta_conn.close()

    return {"rows": len(kpi_df), "weights_sum": sum(WEIGHTS.values())}


if __name__ == "__main__":
    summary = build_kpi()
    print(f"fact_institution_kpi built: {summary['rows']} rows (weights sum to {summary['weights_sum']})")
