"""
pipelines/gold/build_kpi.py

Computes fact_institution_kpi: the weighted Institutional Success Index
composite, one row per (college, academic period), plus its component
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

-- Redesign note (P2 -- KPI Redesign) ---------------------------------------
Two of the original six sub-metrics were doing double duty as both a
"how are we changing" signal and a "how stable are we" signal, with the
direction information thrown away before it ever reached a dashboard:

  * `enrollment_stability` was already magnitude-only (it used
    `.abs()`) -- a college that grew 20% and one that shrank 20% scored
    identically. That's a real question ("is the direction of change a
    concern?") silently discarded. It is now split into:
      - `enrollment_growth`: SIGNED period-over-period % change.
        Informational only -- it does not feed the composite, because
        growth has no universal "good" direction at the institution-KPI
        grain (rapid growth strains capacity the same way decline
        erodes it; it's the college's job to interpret the sign, not
        this pipeline's).
      - `enrollment_volatility`: the old magnitude-only computation,
        renamed to match what it actually measures, clipped to [0, 1].
        This is what feeds the composite (inverted, see
        `compute_success_rate`), because *magnitude* of swing -- not
        its direction -- is what threatens semester-to-semester
        capacity planning.

  * `shifter_stability` only ever counted students LEAVING a college
    (`fact_shifter` joined via `from_program_key`). There was no
    Incoming signal at all, and -- a modeling gap worth naming
    explicitly -- a same-college program switch (e.g. BS CS -> BS IT,
    both under CICT) was being counted as an outgoing shift even though
    it doesn't change that college's population at all. Both are fixed
    together: shift counts are now scoped to CROSS-COLLEGE moves only
    (`from_college_key != to_college_key`), and split into:
      - `outgoing_shift_count` (renamed from `shifter_count`, now
        cross-college-only -- this is a corrected value, not just a
        rename, so historical counts computed before this change will
        differ slightly)
      - `incoming_shift_count` (new)
      - `net_shift_flow` = incoming - outgoing (informational only,
        same reasoning as growth: net inflow isn't inherently "success")
    `shifter_stability` keeps its name and formula shape (it still
    feeds the composite at its original 0.05 weight) but is now
    computed from the corrected `outgoing_shift_count`.

The composite itself is renamed `success_rate` -> `institutional_success_index`
throughout the stack (DB column, ORM, dbt marts, dashboard) -- see
docs/09_Data_Science.md and migrations/versions/0017_kpi_redesign.py.
This is a real rename, not a relabel: leaving the column named
`success_rate` while every consumer displayed "Institutional Success
Index" would recreate the exact doc/schema mismatch already found
elsewhere in this repo (dim_calendar, configs/business_rules.yaml).
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
    "enrollment_volatility": 0.05,
}


def _validate_weights(weights: Dict[str, float]) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > 0.0001:
        raise ConfigError(f"Institutional Success Index weights must sum to 1.0, got {total}")


def compute_success_rate(
    retention_rate: float,
    graduation_rate: float,
    dropout_rate: float,
    shifter_stability: float,
    enrollment_volatility: float,
    program_completion_momentum: float,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """The weighted Institutional Success Index composite.

    `weights` defaults to the module-level `WEIGHTS` but can be
    overridden -- e.g. by scripts/kpi_weight_sensitivity.py, which
    reuses this exact formula under alternate weight vectors instead of
    re-deriving it, so the sensitivity analysis can never silently
    drift from the production formula.

    `enrollment_volatility` is inverted (`1 - enrollment_volatility`)
    the same way `dropout_rate` is inverted above it: both are
    "lower is better" raw measures, and the composite is defined in
    "higher is better" terms throughout.
    """
    weights = weights or WEIGHTS
    _validate_weights(weights)
    score = (
        weights["graduation_rate"] * graduation_rate
        + weights["retention_rate"] * retention_rate
        + weights["dropout_rate"] * (1 - dropout_rate)
        + weights["program_completion_momentum"] * program_completion_momentum
        + weights["shifter_stability"] * shifter_stability
        + weights["enrollment_volatility"] * (1 - enrollment_volatility)
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

    # P2.2: shift counts are scoped to CROSS-COLLEGE moves only. A
    # same-college program switch (from_college_key == to_college_key)
    # doesn't change that college's population and shouldn't count as
    # either an outgoing or incoming shift for a college-grain metric.
    program_college_by_key = dict(zip(dim_program["program_key"], dim_program["college_key"]))
    fact_shifter = fact_shifter.copy()
    fact_shifter["from_college_key"] = fact_shifter["from_program_key"].map(program_college_by_key)
    fact_shifter["to_college_key"] = fact_shifter["to_program_key"].map(program_college_by_key)
    cross_college_shifts = fact_shifter[
        fact_shifter["from_college_key"] != fact_shifter["to_college_key"]
    ]

    enrollment_counts = fact_enrollment.groupby(["college_key", "academic_period_key"]).size().rename("enrollment_count")
    eligible_counts = fact_enrollment[fact_enrollment["is_eligible_to_graduate"]].groupby(
        ["college_key", "academic_period_key"]
    ).size().rename("eligible_count")
    graduation_counts = fact_graduation.groupby(["college_key", "academic_period_key"]).size().rename("graduation_count")
    dropout_counts = fact_dropout.groupby(["college_key", "academic_period_key"]).size().rename("dropout_count")
    outgoing_shift_counts = cross_college_shifts.groupby(
        ["from_college_key", "academic_period_key"]
    ).size().rename("outgoing_shift_count").rename_axis(["college_key", "academic_period_key"])
    incoming_shift_counts = cross_college_shifts.groupby(
        ["to_college_key", "academic_period_key"]
    ).size().rename("incoming_shift_count").rename_axis(["college_key", "academic_period_key"])
    retention_rates = fact_retention.groupby(["college_key", "academic_period_key"])["is_retained"].mean().rename("retention_rate")
    momentum_df = compute_program_completion_momentum(
        fact_enrollment, dim_academic_period, conn
    ).set_index(["college_key", "academic_period_key"])["momentum"]

    kpi = pd.concat(
        [enrollment_counts, eligible_counts, graduation_counts, dropout_counts,
         outgoing_shift_counts, incoming_shift_counts, retention_rates, momentum_df],
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
    kpi["outgoing_shift_count"] = pd.to_numeric(kpi["outgoing_shift_count"], errors="coerce").fillna(0)
    kpi["incoming_shift_count"] = pd.to_numeric(kpi["incoming_shift_count"], errors="coerce").fillna(0)
    kpi["retention_rate"] = pd.to_numeric(kpi["retention_rate"], errors="coerce").fillna(0.0)
    kpi["momentum"] = pd.to_numeric(kpi["momentum"], errors="coerce").fillna(0.0)

    # Same root cause, same fix: pd.NA (pandas' generic nullable
    # sentinel) forces whatever column it touches to object dtype the
    # moment it's introduced via .replace(). np.nan is a native float64
    # NaN -- swapping it in keeps this column numeric the entire way
    # through the division and the .fillna(0.0) that follows.
    kpi["graduation_rate"] = (kpi["graduation_count"] / kpi["eligible_count"].replace(0, np.nan)).fillna(0.0).astype(float)
    kpi["dropout_rate"] = kpi["dropout_count"] / kpi["enrollment_count"]
    kpi["net_shift_flow"] = kpi["incoming_shift_count"] - kpi["outgoing_shift_count"]
    # Corrected numerator (cross-college shifts only) -- same formula
    # shape as before, feeds the composite unchanged at its 0.05 weight.
    kpi["shifter_stability"] = 1 - (kpi["outgoing_shift_count"] / kpi["enrollment_count"])

    period_ordinal_by_key = dict(
        zip(dim_academic_period["academic_period_key"], dim_academic_period["period_ordinal"])
    )
    kpi["_period_ordinal"] = kpi["academic_period_key"].map(period_ordinal_by_key)
    kpi = kpi.sort_values(["college_key", "_period_ordinal"]).reset_index(drop=True)
    prior_enrollment = kpi.groupby("college_key")["enrollment_count"].shift(1)
    with pd.option_context("mode.chained_assignment", None):
        delta = kpi["enrollment_count"] - prior_enrollment
        # P2.1: signed growth (informational-only, not clipped -- the
        # composite doesn't consume this) alongside magnitude-only
        # volatility (clipped [0,1], feeds the composite). A college
        # with no prior period to compare against gets 0.0 for both --
        # "no measured change yet", not an assumption of good or bad.
        growth = delta / prior_enrollment
        volatility = delta.abs() / prior_enrollment
    kpi["enrollment_growth"] = growth.fillna(0.0)
    kpi["enrollment_volatility"] = volatility.clip(lower=0.0, upper=1.0).fillna(0.0)

    kpi["institutional_success_index"] = kpi.apply(
        lambda row: compute_success_rate(
            retention_rate=row["retention_rate"],
            graduation_rate=row["graduation_rate"],
            dropout_rate=row["dropout_rate"],
            shifter_stability=row["shifter_stability"],
            enrollment_volatility=row["enrollment_volatility"],
            program_completion_momentum=row["momentum"],
        ),
        axis=1,
    )

    return kpi[[
        "college_key", "academic_period_key", "enrollment_count", "graduation_count", "dropout_count",
        "outgoing_shift_count", "incoming_shift_count", "net_shift_flow", "retention_rate",
        "graduation_rate", "dropout_rate", "shifter_stability", "enrollment_growth",
        "enrollment_volatility", "momentum", "institutional_success_index",
    ]].rename(columns={"momentum": "program_completion_momentum"})


def build_kpi(
    gold_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, object]:
    _validate_weights(WEIGHTS)
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