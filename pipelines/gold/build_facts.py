"""
pipelines/gold/build_facts.py

Builds fact_enrollment, fact_graduation, fact_dropout, fact_shifter, and
fact_retention from Silver data + the Gold dimensions built in
build_dimensions.py.

-- Redesign note (Task 23/24 -- Gold Modeling Fix) --------------------------
Every fact table below now carries a single `academic_period_key` instead
of the old pair `semester_key` + `academic_year_key`. That pair only
existed because dimensions were snowflaked (dim_semester -> dim_academic_year);
now that dim_academic_period is one denormalized row per semester (see
build_dimensions.py's docstring), carrying both keys on every fact would
just be redundant -- academic_year is already reachable via a single join
to dim_academic_period.

`fact_enrollment` now carries `year_level_key` (FK to dim_year_level)
instead of a raw `year_level` int -- see build_dimensions.py's docstring
for why year_level is now a governed dimension. Anything that needs the
*numeric* year_level back (graduation-eligibility, momentum -- both in
build_kpi.py) joins dim_year_level for it; the value isn't lost, just
normalized to one place.

The centerpiece of this file, unchanged from the original design: resolving
student_key for every fact row via an AS-OF join against dim_student's
SCD2 history, not just "the student's current row." A shifted student has
two dim_student rows; an enrollment record from BEFORE their shift must
resolve to the OLD (closed) row, not the current one -- otherwise a
student's pre-shift enrollment history would incorrectly show their
post-shift program.

On "MERGE/upsert" vs. full rebuild: Gold facts are FULLY REBUILT from
Silver on every run -- simpler than incremental merge at this data
volume, and it avoids an entire class of incremental-merge bugs. The
same Silver input always produces the exact same Gold output.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import duckdb
import pandas as pd

from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import ObjectStorage, load_storage_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

STAGE = "gold_build_facts"


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def _load_gold_dimensions(gold_storage: ObjectStorage) -> Dict[str, pd.DataFrame]:
    names = ["dim_student", "dim_program", "dim_college", "dim_academic_period", "dim_year_level"]
    return {name: _read_parquet(gold_storage, f"gold/{name}/data.parquet") for name in names}


def resolve_student_key_as_of(
    df: pd.DataFrame,
    dim_student: pd.DataFrame,
    dim_academic_period: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """AS-OF join: attach the student_key valid AT the row's own
    (academic_year, semester_number) -- not just the student's current
    row. Uses dim_student's [_valid_from_period_key, _valid_to_period_key]
    range (open-ended if _valid_to_period_key is null / current).
    """
    conn.register("df_view", df)
    conn.register("dim_student_view", dim_student)
    conn.register("dim_period_view", dim_academic_period)

    result = conn.execute(
        """
        SELECT df.*, ds.student_key
        FROM df_view df
        JOIN dim_period_view per
            ON df.academic_year = per.academic_year AND df.semester_number = per.semester_number
        JOIN dim_student_view ds
            ON df.student_id = ds.student_id
            AND per.academic_period_key >= ds._valid_from_period_key
            AND (ds._valid_to_period_key IS NULL OR per.academic_period_key <= ds._valid_to_period_key)
        """
    ).df()

    conn.unregister("df_view")
    conn.unregister("dim_student_view")
    conn.unregister("dim_period_view")
    return result


def _attach_period_key(df: pd.DataFrame, dim_academic_period: pd.DataFrame) -> pd.DataFrame:
    lookup = dim_academic_period.set_index(["academic_year", "semester_number"])["academic_period_key"]
    df = df.copy()
    df["academic_period_key"] = df.set_index(["academic_year", "semester_number"]).index.map(lookup)
    return df


def build_fact_enrollment(
    enrollment_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(enrollment_df, dims["dim_student"], dims["dim_academic_period"], conn)
    df = _attach_period_key(df, dims["dim_academic_period"])

    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    year_level_key_by_level = dict(zip(dims["dim_year_level"]["year_level"], dims["dim_year_level"]["year_level_key"]))

    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)
    df["year_level_key"] = df["year_level"].map(year_level_key_by_level)

    # P1 fix (build_kpi.py undercount bug): year_level_key already had this
    # guard; program_key, college_key, and academic_period_key did not.
    # A row with any of these unmapped (a program_id/college_id not in the
    # dimension, or an (academic_year, semester_number) not in
    # dim_academic_period) got a silent NaN here. That row still passed
    # through to fact_enrollment -- but pandas' groupby(...).size() in
    # build_kpi.py drops NaN-keyed rows by default (dropna=True), so the
    # row silently vanished from fact_institution_kpi.enrollment_count
    # while still existing in fact_enrollment itself. That's exactly the
    # shape of bug that produced mart_executive_summary's enrollment total
    # (sum of the KPI's per-group counts) undercounting gold.fact_enrollment's
    # actual row count. Same fix as year_level_key: fail loudly, don't
    # silently drop rows out of downstream aggregates.
    unmapped_program = df["program_key"].isna().sum()
    if unmapped_program:
        raise ValueError(
            f"{unmapped_program} fact_enrollment row(s) have a program_id not present in "
            "dim_program -- fix the source data or dim_program's build, don't silently drop them."
        )
    unmapped_college = df["college_key"].isna().sum()
    if unmapped_college:
        raise ValueError(
            f"{unmapped_college} fact_enrollment row(s) have a college_id not present in "
            "dim_college -- fix the source data or dim_college's build, don't silently drop them."
        )
    unmapped_period = df["academic_period_key"].isna().sum()
    if unmapped_period:
        raise ValueError(
            f"{unmapped_period} fact_enrollment row(s) have an (academic_year, semester_number) not "
            "present in dim_academic_period -- fix the source data or dim_academic_period's build, "
            "don't silently drop them."
        )
    unmapped = df["year_level_key"].isna().sum()
    if unmapped:
        raise ValueError(
            f"{unmapped} fact_enrollment row(s) have a year_level outside dim_year_level's "
            "governed domain -- extend YEAR_LEVEL_LABELS in build_dimensions.py, don't silently drop them."
        )

    return df[["student_key", "program_key", "college_key", "academic_period_key",
               "enrollment_status", "year_level_key", "units_enrolled", "is_new_enrollee"]].reset_index(drop=True)


def build_fact_graduation(
    graduation_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(graduation_df, dims["dim_student"], dims["dim_academic_period"], conn)
    df = _attach_period_key(df, dims["dim_academic_period"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df[["student_key", "program_key", "college_key", "academic_period_key",
               "years_to_complete"]].reset_index(drop=True)


def build_fact_dropout(
    dropout_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(dropout_df, dims["dim_student"], dims["dim_academic_period"], conn)
    df = _attach_period_key(df, dims["dim_academic_period"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df[["student_key", "program_key", "college_key", "academic_period_key",
               "dropout_reason", "semesters_completed_before_dropout"]].reset_index(drop=True)


def build_fact_shifter(
    shifter_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(shifter_df, dims["dim_student"], dims["dim_academic_period"], conn)
    df = _attach_period_key(df, dims["dim_academic_period"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    df["from_program_key"] = df["from_program_id"].map(program_key_by_id)
    df["to_program_key"] = df["to_program_id"].map(program_key_by_id)
    return df[["student_key", "from_program_key", "to_program_key",
               "academic_period_key"]].reset_index(drop=True)


def build_fact_retention(fact_enrollment: pd.DataFrame, dim_academic_period: pd.DataFrame) -> pd.DataFrame:
    """One row per student per semester where a 'did they continue' question
    is even answerable: excludes GRADUATED rows and the final observed
    semester (no next semester to check).

    "Immediately following semester" is resolved via `period_ordinal + 1`,
    NOT `academic_period_key + 1` -- the two happen to coincide today
    because dim_academic_period's keys are assigned in chronological
    order, but the ordinal is the column that actually *means*
    "next chronological period"; the key is just an opaque surrogate.
    Depending on key arithmetic to encode chronology is a latent bug
    waiting for the day someone re-keys or re-sorts the dimension.
    """
    ordinal_by_period_key = dict(
        zip(dim_academic_period["academic_period_key"], dim_academic_period["period_ordinal"])
    )
    max_ordinal = dim_academic_period["period_ordinal"].max()

    fact_enrollment = fact_enrollment.copy()
    fact_enrollment["_period_ordinal"] = fact_enrollment["academic_period_key"].map(ordinal_by_period_key)

    next_status = fact_enrollment.set_index(["student_key", "_period_ordinal"])["enrollment_status"]

    eligible = fact_enrollment[
        (fact_enrollment["enrollment_status"] != "GRADUATED")
        & (fact_enrollment["_period_ordinal"] < max_ordinal)
    ].copy()

    def _is_retained(row) -> int:
        next_key = (row["student_key"], row["_period_ordinal"] + 1)
        status = next_status.get(next_key)
        return int(status in ("ENROLLED", "GRADUATED"))

    eligible["is_retained"] = eligible.apply(_is_retained, axis=1)
    return eligible[["student_key", "program_key", "college_key",
                      "academic_period_key", "is_retained"]].reset_index(drop=True)


def _write_and_log(gold_storage, meta_conn, name: str, df: pd.DataFrame, rows_in: int) -> None:
    _write_parquet(gold_storage, f"gold/{name}/data.parquet", df)
    record_run(
        meta_conn, str(uuid.uuid4()), batch_id=str(uuid.uuid4()), stage=STAGE, entity=name,
        partition_key="all", started_at=datetime.now(timezone.utc), status="SUCCESS",
        rows_in=rows_in, rows_out=len(df), source_path="silver/*, gold/dim_*",
    )


def build_all_facts(
    silver_storage: Optional[ObjectStorage] = None,
    gold_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, int]:
    silver_storage = silver_storage or load_storage_from_env(DEFAULT_SILVER_STORAGE_PATH, "MINIO_SILVER_BUCKET")
    gold_storage = gold_storage or load_storage_from_env(DEFAULT_GOLD_STORAGE_PATH, "MINIO_GOLD_BUCKET")
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    dims = _load_gold_dimensions(gold_storage)
    enrollment_df = _read_parquet(silver_storage, "silver/enrollment/data.parquet")
    graduation_df = _read_parquet(silver_storage, "silver/graduation/data.parquet")
    dropout_df = _read_parquet(silver_storage, "silver/dropout/data.parquet")
    shifter_df = _read_parquet(silver_storage, "silver/shifter/data.parquet")

    conn = duckdb.connect(":memory:")

    fact_enrollment = build_fact_enrollment(enrollment_df, dims, conn)
    _write_and_log(gold_storage, meta_conn, "fact_enrollment", fact_enrollment, len(enrollment_df))

    fact_graduation = build_fact_graduation(graduation_df, dims, conn)
    _write_and_log(gold_storage, meta_conn, "fact_graduation", fact_graduation, len(graduation_df))

    fact_dropout = build_fact_dropout(dropout_df, dims, conn)
    _write_and_log(gold_storage, meta_conn, "fact_dropout", fact_dropout, len(dropout_df))

    fact_shifter = build_fact_shifter(shifter_df, dims, conn)
    _write_and_log(gold_storage, meta_conn, "fact_shifter", fact_shifter, len(shifter_df))

    fact_retention = build_fact_retention(fact_enrollment, dims["dim_academic_period"])
    _write_and_log(gold_storage, meta_conn, "fact_retention", fact_retention, len(fact_enrollment))

    conn.close()
    if owns_conn:
        meta_conn.close()

    return {
        "fact_enrollment": len(fact_enrollment),
        "fact_graduation": len(fact_graduation),
        "fact_dropout": len(fact_dropout),
        "fact_shifter": len(fact_shifter),
        "fact_retention": len(fact_retention),
    }


if __name__ == "__main__":
    import uuid as _uuid
    from pipelines.common.logging_config import PipelineStageLogger, get_logger

    _logger = get_logger(__name__)
    _run_id = str(_uuid.uuid4())
    with PipelineStageLogger(_run_id, stage="gold") as stage_log:
        counts = build_all_facts()
        stage_log.rows_processed = sum(counts.values())
        _logger.info("Gold fact build complete: %s", counts, extra={"pipeline_extra": counts})