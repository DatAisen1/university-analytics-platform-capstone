"""
pipelines/gold/build_kpi.py

Computes fact_institution_kpi: the weighted Success Rate composite,
one row per (college, academic period), plus its six component
sub-metrics.

-- Redesign note (Task 23/24 -- Gold Modeling Fix) --------------------------
Grain key renamed `semester_key` -> `academic_period_key`.

fact_enrollment now carries `year_level_key` instead of a raw `year_level`
int. Both KPI components that need the *numeric* year_level -- graduation
eligibility and program-completion momentum -- join dim_year_level once,
right here, to recover it. This is the normal dimensional-modeling
pattern for "a measure computation needs a dimension's ordinal
attribute": the value isn't duplicated on the fact AND the dimension,
it's read from the one place it's governed.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import duckdb
import numpy as np
import pandas as pd

from pipelines.common.config import ConfigError
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import ObjectStorage, load_storage_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

STAGE = "gold_build_kpi"

WEIGHTS = {
    "graduation_rate": 0.30,
    "retention_rate": 0.25,
    "dropout_rate": 0.20,
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


def _attach_numeric_year_level(fact_enrollment: pd.DataFrame, dim_year_level: pd.DataFrame) -> pd.DataFrame:
    """Recover the numeric year_level from its governed dimension."""
    year_level_by_key = dict(zip(dim_year_level["year_level_key"], dim_year_level["year_level"]))
    fact_enrollment = fact_enrollment.copy()
    fact_enrollment["year_level"] = fact_enrollment["year_level_key"].map(year_level_by_key)
    return fact_enrollment


def compute_program_completion_momentum(
    fact_enrollment: pd.DataFrame, dim_academic_period: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """Per (college_key, academic_period_key): fraction of continuing
    students whose year_level increased vs. the immediately prior period,
    matched via `period_ordinal` (chronological), not surrogate-key
    arithmetic.
    """
    period_ordinal_by_key = dict(
        zip(dim_academic_period["academic_period_key"], dim_academic_period["period_ordinal"])
    )
    fact_enrollment = fact_enrollment.copy()
    fact_enrollment["period_ordinal"] = fact_enrollment["academic_period_key"].map(period_ordinal_by_key)

    conn.register("fe", fact_enrollment)
    result = conn.execute(
        """
        SELECT
            curr.college_key,
            curr.academic_period_key,
            AVG(CASE WHEN curr.year_level > prev.year_level THEN 1.0 ELSE 0.0 END) AS momentum
        FROM fe curr
        JOIN fe prev
            ON curr.student_key = prev.student_key
            AND curr.period_ordinal = prev.period_ordinal + 1
        GROUP BY curr.college_key, curr.academic_period_key
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
    dim_year_level: pd.DataFrame,
    dim_academic_period: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    fact_enrollment = _attach_numeric_year_level(fact_enrollment, dim_year_level)

    nominal_duration_by_program_key = dict(
        zip(dim_program["program_key"], dim_program["nominal_duration_years"])
    )
    fact_enrollment["nominal_duration_years"] = fact_enrollment["program_key"].map(nominal_duration_by_program_key)
    fact_enrollment["is_eligible_to_graduate"] = (
        fact_enrollment["year_level"] >= fact_enrollment["nominal_duration_years"].apply(
            lambda x: int(x) if x == int(x) else int(x) + 1
        )
    )

    program_college_by_key = dict(zip(dim_program["program_key"], dim_program["college_key"]))
    fact_shifter = fact_shifter.copy()
    fact_shifter["college_key"] = fact_shifter["from_program_key"].map(program_college_by_key)

    enrollment_counts = fact_enrollment.groupby(["college_key", "academic_period_key"]).size().rename("enrollment_count")
    eligible_counts = fact_enrollment[fact_enrollment["is_eligible_to_graduate"]].groupby(
        ["college_key", "academic_period_key"]
    ).size().rename("eligible_count")
    graduation_counts = fact_graduation.groupby(["college_key", "academic_period_key"]).size().rename("graduation_count")
    dropout_counts = fact_dropout.groupby(["college_key", "academic_period_key"]).size().rename("dropout_count")
    shifter_counts = fact_shifter.groupby(["college_key", "academic_period_key"]).size().rename("shifter_count")
    retention_rates = fact_retention.groupby(["college_key", "academic_period_key"])["is_retained"].mean().rename("retention_rate")
    momentum_df = compute_program_completion_momentum(
        fact_enrollment, dim_academic_period, conn
    ).set_index(["college_key", "academic_period_key"])["momentum"]

    kpi = pd.concat(
        [enrollment_counts, eligible_counts, graduation_counts, dropout_counts,
         shifter_counts, retention_rates, momentum_df],
        axis=1,
    ).reset_index()

    # P3 fix: these six columns come out of pd.concat([...], axis=1) --
    # a mix of groupby().size() (int64) and groupby().mean() (float64)
    # results re-indexed against each other, so a college/period
    # combination present in one Series but not another becomes NaN
    # here. pd.to_numeric(..., errors="coerce") guarantees each column
    # is a genuine numpy float64 BEFORE .fillna() runs -- closing off
    # the actual root cause of the FutureWarning this used to raise:
    # pandas warns when .fillna() is called on an *object-dtype* column
    # (which happens whenever any of these columns picks up pandas'
    # generic pd.NA sentinel, or an ambiguous dtype from concat, instead
    # of a plain float NaN) because a future pandas release will stop
    # silently downcasting the result back to a numeric dtype. Forcing
    # numeric dtype here isn't a warnings-suppression band-aid -- it's
    # removing the only path that could produce an object-dtype column
    # in the first place, so there is nothing left to downcast.
    kpi["eligible_count"] = pd.to_numeric(kpi["eligible_count"], errors="coerce").fillna(0)
    kpi["graduation_count"] = pd.to_numeric(kpi["graduation_count"], errors="coerce").fillna(0)
    kpi["dropout_count"] = pd.to_numeric(kpi["dropout_count"], errors="coerce").fillna(0)
    kpi["shifter_count"] = pd.to_numeric(kpi["shifter_count"], errors="coerce").fillna(0)
    kpi["retention_rate"] = pd.to_numeric(kpi["retention_rate"], errors="coerce").fillna(0.0)
    kpi["momentum"] = pd.to_numeric(kpi["momentum"], errors="coerce").fillna(0.0)

    # Same root cause, same fix: pd.NA (pandas' generic nullable
    # sentinel) forces whatever column it touches to object dtype the
    # moment it's introduced via .replace(). np.nan is a native float64
    # NaN -- swapping it in keeps this column numeric the entire way
    # through the division and the .fillna(0.0) that follows.
    kpi["graduation_rate"] = (kpi["graduation_count"] / kpi["eligible_count"].replace(0, np.nan)).fillna(0.0).astype(float)
    kpi["dropout_rate"] = kpi["dropout_count"] / kpi["enrollment_count"]
    kpi["shifter_stability"] = 1 - (kpi["shifter_count"] / kpi["enrollment_count"])

    period_ordinal_by_key = dict(
        zip(dim_academic_period["academic_period_key"], dim_academic_period["period_ordinal"])
    )
    kpi["_period_ordinal"] = kpi["academic_period_key"].map(period_ordinal_by_key)
    kpi = kpi.sort_values(["college_key", "_period_ordinal"]).reset_index(drop=True)
    prior_enrollment = kpi.groupby("college_key")["enrollment_count"].shift(1)
    with pd.option_context("mode.chained_assignment", None):
        stability = 1 - (kpi["enrollment_count"] - prior_enrollment).abs() / prior_enrollment
    kpi["enrollment_stability"] = stability.clip(lower=0.0, upper=1.0).fillna(1.0)

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
        "college_key", "academic_period_key", "enrollment_count", "graduation_count", "dropout_count",
        "shifter_count", "retention_rate", "graduation_rate", "dropout_rate", "shifter_stability",
        "enrollment_stability", "momentum", "success_rate",
    ]].rename(columns={"momentum": "program_completion_momentum"})


def build_kpi(
    gold_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, object]:
    _validate_weights()
    gold_storage = gold_storage or load_storage_from_env(DEFAULT_GOLD_STORAGE_PATH, "MINIO_GOLD_BUCKET")
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    fact_enrollment = _read_parquet(gold_storage, "gold/fact_enrollment/data.parquet")
    fact_graduation = _read_parquet(gold_storage, "gold/fact_graduation/data.parquet")
    fact_dropout = _read_parquet(gold_storage, "gold/fact_dropout/data.parquet")
    fact_shifter = _read_parquet(gold_storage, "gold/fact_shifter/data.parquet")
    fact_retention = _read_parquet(gold_storage, "gold/fact_retention/data.parquet")
    dim_program = _read_parquet(gold_storage, "gold/dim_program/data.parquet")
    dim_year_level = _read_parquet(gold_storage, "gold/dim_year_level/data.parquet")
    dim_academic_period = _read_parquet(gold_storage, "gold/dim_academic_period/data.parquet")

    conn = duckdb.connect(":memory:")
    kpi_df = build_fact_institution_kpi(
        fact_enrollment, fact_graduation, fact_dropout, fact_shifter, fact_retention,
        dim_program, dim_year_level, dim_academic_period, conn,
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