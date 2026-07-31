"""
pipelines/gold/build_facts.py

Builds fact_enrollment, fact_graduation, fact_dropout, fact_shifter, and
fact_retention from Silver data + the Gold dimensions built in Day 12.

The one piece of real design care in this file: resolving student_key for
every fact row via an AS-OF join against dim_student's SCD2 history, not
just "the student's current row." A shifted student has two dim_student
rows; an enrollment record from BEFORE their shift must resolve to the
OLD (closed) row, not the current one -- otherwise a student's pre-shift
enrollment history would incorrectly show their post-shift program. This
is exactly the point of building SCD2 in the first place (Day 12); this
is where it actually gets used, not just stored.

On "MERGE/upsert" vs. full rebuild: docs/12_Implementation_Roadmap.md
describes fact loads as MERGE/upsert keyed on natural key + semester, which
assumes an incrementally-maintained warehouse table. This project's Gold
facts are instead FULLY REBUILT from Silver on every run -- a full rebuild
at this data volume (tens of thousands of rows) is simpler than an
incremental merge and carries none of an incremental merge's failure
modes (partial updates, forgotten backfills, drift between merge logic
and full-rebuild logic). Idempotency is achieved differently but no less
genuinely: the same Silver input always produces the exact same Gold
output, so re-running never duplicates anything -- proven by a test that
runs the build twice and asserts identical row counts and content, not
just "the row count is stable" superficially. This mirrors the same
full-recompute reasoning docs/06_Data_Warehouse.md already applies to
fact_institution_kpi, now extended to the base facts too.
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
from pipelines.common.storage import LocalFileStorage, ObjectStorage

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
    names = ["dim_student", "dim_program", "dim_college", "dim_semester", "dim_academic_year"]
    return {name: _read_parquet(gold_storage, f"gold/{name}/data.parquet") for name in names}


def resolve_student_key_as_of(
    df: pd.DataFrame, dim_student: pd.DataFrame, dim_semester: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """AS-OF join: attach the student_key valid AT the row's own
    (academic_year, semester_number) -- not just the student's current
    row. Uses dim_student's [_valid_from_semester_key, _valid_to_semester_key]
    range (open-ended if _valid_to_semester_key is null / current).
    """
    conn.register("df_view", df)
    conn.register("dim_student_view", dim_student)
    conn.register("dim_semester_view", dim_semester)

    result = conn.execute(
        """
        SELECT df.*, ds.student_key
        FROM df_view df
        JOIN dim_semester_view sem
            ON df.academic_year = sem.academic_year AND df.semester_number = sem.semester_number
        JOIN dim_student_view ds
            ON df.student_id = ds.student_id
            AND sem.semester_key >= ds._valid_from_semester_key
            AND (ds._valid_to_semester_key IS NULL OR sem.semester_key <= ds._valid_to_semester_key)
        """
    ).df()

    conn.unregister("df_view")
    conn.unregister("dim_student_view")
    conn.unregister("dim_semester_view")
    return result


def _attach_semester_key(df: pd.DataFrame, dim_semester: pd.DataFrame) -> pd.DataFrame:
    lookup = dim_semester.set_index(["academic_year", "semester_number"])[["semester_key", "academic_year_key"]]
    keys = df.set_index(["academic_year", "semester_number"]).index.map(lambda k: lookup.loc[k])
    df = df.copy()
    df["semester_key"] = [k["semester_key"] for k in keys]
    df["academic_year_key"] = [k["academic_year_key"] for k in keys]
    return df


def build_fact_enrollment(
    enrollment_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(enrollment_df, dims["dim_student"], dims["dim_semester"], conn)
    df = _attach_semester_key(df, dims["dim_semester"])

    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)

    return df[["student_key", "program_key", "college_key", "semester_key", "academic_year_key",
               "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"]].reset_index(drop=True)


def build_fact_graduation(
    graduation_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(graduation_df, dims["dim_student"], dims["dim_semester"], conn)
    df = _attach_semester_key(df, dims["dim_semester"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df[["student_key", "program_key", "college_key", "semester_key", "academic_year_key",
               "years_to_complete"]].reset_index(drop=True)


def build_fact_dropout(
    dropout_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(dropout_df, dims["dim_student"], dims["dim_semester"], conn)
    df = _attach_semester_key(df, dims["dim_semester"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    college_key_by_id = dict(zip(dims["dim_college"]["college_id"], dims["dim_college"]["college_key"]))
    df["program_key"] = df["program_id"].map(program_key_by_id)
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df[["student_key", "program_key", "college_key", "semester_key", "academic_year_key",
               "dropout_reason", "semesters_completed_before_dropout"]].reset_index(drop=True)


def build_fact_shifter(
    shifter_df: pd.DataFrame, dims: Dict[str, pd.DataFrame], conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    df = resolve_student_key_as_of(shifter_df, dims["dim_student"], dims["dim_semester"], conn)
    df = _attach_semester_key(df, dims["dim_semester"])
    program_key_by_id = dict(zip(dims["dim_program"]["program_id"], dims["dim_program"]["program_key"]))
    df["from_program_key"] = df["from_program_id"].map(program_key_by_id)
    df["to_program_key"] = df["to_program_id"].map(program_key_by_id)
    return df[["student_key", "from_program_key", "to_program_key",
               "semester_key", "academic_year_key"]].reset_index(drop=True)


def build_fact_retention(fact_enrollment: pd.DataFrame, dim_semester: pd.DataFrame) -> pd.DataFrame:
    """One row per student per semester where a 'did they continue' question
    is even answerable: excludes GRADUATED rows (nothing to retain past
    graduation) and the final observed semester (2024-2 -- no next semester
    exists to check, so retention there is undefined, not 'not retained').
    is_retained = 1 if the student has an ENROLLED or GRADUATED record in
    the immediately following semester, else 0 (they dropped or simply
    have no further record).
    """
    max_semester_key = dim_semester["semester_key"].max()

    next_status = fact_enrollment.set_index(["student_key", "semester_key"])["enrollment_status"]

    eligible = fact_enrollment[
        (fact_enrollment["enrollment_status"] != "GRADUATED")
        & (fact_enrollment["semester_key"] < max_semester_key)
    ].copy()

    def _is_retained(row) -> int:
        next_key = (row["student_key"], row["semester_key"] + 1)
        status = next_status.get(next_key)
        return int(status in ("ENROLLED", "GRADUATED"))

    eligible["is_retained"] = eligible.apply(_is_retained, axis=1)
    return eligible[["student_key", "program_key", "college_key", "semester_key",
                      "academic_year_key", "is_retained"]].reset_index(drop=True)


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
    silver_storage = silver_storage or LocalFileStorage(DEFAULT_SILVER_STORAGE_PATH)
    gold_storage = gold_storage or LocalFileStorage(DEFAULT_GOLD_STORAGE_PATH)
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

    fact_retention = build_fact_retention(fact_enrollment, dims["dim_semester"])
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
    counts = build_all_facts()
    print("Gold fact build complete:")
    for name, count in counts.items():
        print(f"  {name}: {count} rows")
