"""
pipelines/gold/build_dimensions.py

Builds the Gold star-schema dimension tables from Silver data:
dim_college, dim_program, dim_academic_year, dim_semester, dim_calendar,
and dim_student (the one with real SCD Type 2 history).

A note on tooling choice, since this project has been deliberate about
"DuckDB SQL for transforms" everywhere else: dim_student's SCD2
construction is written in plain Python, not DuckDB SQL. Cleaning
(Day 10) and dedup (Day 11) are naturally set-based operations -- exactly
what SQL is good at. SCD2 history-building is inherently SEQUENTIAL per
student (open a row, watch for a shift event, close the row, open the
next one) -- forcing that into a single window-function SQL expression
would trade clarity for a stylistic consistency that isn't worth it here.
Using the right tool per sub-problem, not the same tool everywhere, is
the actual engineering judgment; see docs/07_Technology_Stack.md's
broader "match tool complexity to the problem" theme.

A second adaptation from the original design doc worth being explicit
about: docs/04_Data_Modeling.md originally specified `_valid_from DATE`/
`_valid_to DATE` for dim_student's SCD2 columns. This project has no
literal date fields anywhere in its model -- only academic_year +
semester_number. So SCD2 validity here is expressed as
`_valid_from_semester_key` / `_valid_to_semester_key`, both FKs into
dim_semester, which is the honest equivalent given what the data actually
contains, not a deviation for its own sake.

Postgres is not running in this environment (no Docker daemon -- see
Day 2's note). Gold tables are written to warehouse/gold_store/ as
Parquet via the same ObjectStorage abstraction used for Bronze/Silver;
materializing them into the real Postgres warehouse is Week 3's job
(Day 15+), and only the storage target changes, not this logic.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage, ObjectStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

STAGE = "gold_build_dimensions"

ACADEMIC_YEARS = [2021, 2022, 2023, 2024]


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def semester_ordinal(academic_year: int, semester_number: int) -> int:
    return (academic_year - 2021) * 2 + (semester_number - 1)


# ---------------------------------------------------------------------------
# Static / derived dimensions (no Silver source -- these are structural)
# ---------------------------------------------------------------------------

def build_dim_academic_year() -> pd.DataFrame:
    rows = [
        {"academic_year_key": i + 1, "year_label": f"{y}-{y+1}", "start_year": y, "end_year": y + 1}
        for i, y in enumerate(ACADEMIC_YEARS)
    ]
    return pd.DataFrame(rows)


def build_dim_semester(dim_academic_year: pd.DataFrame) -> pd.DataFrame:
    year_key_by_year = dict(zip(dim_academic_year["start_year"], dim_academic_year["academic_year_key"]))
    rows = []
    key = 1
    for y in ACADEMIC_YEARS:
        for s in (1, 2):
            rows.append({
                "semester_key": key,
                "semester_id": f"{y}-{s}",
                "academic_year": y,
                "semester_number": s,
                "academic_year_key": year_key_by_year[y],
            })
            key += 1
    return pd.DataFrame(rows)


def semester_key_lookup(dim_semester: pd.DataFrame) -> Dict[tuple, int]:
    return {
        (row["academic_year"], row["semester_number"]): row["semester_key"]
        for _, row in dim_semester.iterrows()
    }


def build_dim_calendar(dim_semester: pd.DataFrame) -> pd.DataFrame:
    """A day-grain calendar dimension for 2021-01-01 through 2024-12-31.

    Disclosed simplification: the project brief specifies academic years
    and two semesters per year, but never literal semester start/end
    dates. This dimension assumes semester 1 = Jan 1 - Jun 30 and
    semester 2 = Jul 1 - Dec 31 of the SAME calendar year, matching how
    academic_year is already used elsewhere in this project as a single
    calendar year (not a "2021-2022"-style split year). A real
    deployment would replace this assumption with the institution's
    actual registrar calendar.
    """
    semester_key_by = semester_key_lookup(dim_semester)
    dates = pd.date_range("2021-01-01", "2024-12-31", freq="D")

    rows = []
    for date_key, d in enumerate(dates, start=1):
        semester_number = 1 if d.month <= 6 else 2
        is_start = (d.month, d.day) in {(1, 1), (7, 1)}
        is_end = (d.month, d.day) in {(6, 30), (12, 31)}
        rows.append({
            "date_key": date_key,
            "full_date": d.date(),
            "year": d.year,
            "quarter": (d.month - 1) // 3 + 1,
            "month": d.month,
            "day": d.day,
            "is_semester_start": is_start,
            "is_semester_end": is_end,
            "semester_key": semester_key_by[(d.year, semester_number)],
            "academic_year": d.year,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Reference-data-backed dimensions
# ---------------------------------------------------------------------------

def build_dim_college(college_df: pd.DataFrame) -> pd.DataFrame:
    df = college_df[["college_id", "college_name"]].drop_duplicates().reset_index(drop=True)
    df.insert(0, "college_key", range(1, len(df) + 1))
    return df


def build_dim_program(program_df: pd.DataFrame, dim_college: pd.DataFrame) -> pd.DataFrame:
    college_key_by_id = dict(zip(dim_college["college_id"], dim_college["college_key"]))
    df = program_df[["program_id", "program_name", "college_id", "program_level",
                      "nominal_duration_years"]].drop_duplicates().reset_index(drop=True)
    df.insert(0, "program_key", range(1, len(df) + 1))
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df


# ---------------------------------------------------------------------------
# dim_student -- SCD Type 2
# ---------------------------------------------------------------------------

def build_dim_student(
    student_df: pd.DataFrame, shifter_df: pd.DataFrame, dim_semester: pd.DataFrame
) -> pd.DataFrame:
    """Build the full SCD2 history for every student: one row per distinct
    (program) period they were in, opened/closed at shift-event
    boundaries. A student with zero shift events gets exactly one row,
    open-ended (_is_current=True, _valid_to_semester_key=None).
    """
    sem_key = semester_key_lookup(dim_semester)

    shifts_by_student: Dict[str, List[dict]] = {}
    for _, row in shifter_df.sort_values(["academic_year", "semester_number"]).iterrows():
        shifts_by_student.setdefault(row["student_id"], []).append(row.to_dict())

    all_rows: List[dict] = []
    for _, student in student_df.iterrows():
        sid = student["student_id"]
        entry_year = int(student["cohort_academic_year"])
        entry_ordinal = semester_ordinal(entry_year, 1)
        current_program_id = student["entry_program_id"]
        current_college_id = student["entry_college_id"]
        valid_from_key = sem_key[(entry_year, 1)]

        for shift in shifts_by_student.get(sid, []):
            shift_ordinal = semester_ordinal(int(shift["academic_year"]), int(shift["semester_number"]))

            if shift_ordinal == entry_ordinal:
                # The shift happens in the student's very first observed
                # semester -- there is no distinguishable "pre-shift"
                # period in the data at all (Day 5's simulate_student
                # applies the shift check before emitting that semester's
                # enrollment record, so the first record already reflects
                # the post-shift program). Don't open a doomed-to-be-closed
                # row with no valid prior semester to close it at --
                # just carry the post-shift program forward as if it were
                # the entry program from the start.
                current_program_id = shift["to_program_id"]
                continue

            close_ordinal = shift_ordinal - 1
            close_year = 2021 + close_ordinal // 2
            close_sem = (close_ordinal % 2) + 1
            valid_to_key = sem_key.get((close_year, close_sem))

            all_rows.append({
                "student_id": sid, "gender": student["gender"], "birth_year": student["birth_year"],
                "home_province": student["home_province"], "admission_type": student["admission_type"],
                "college_id": current_college_id, "program_id": current_program_id,
                "_valid_from_semester_key": valid_from_key, "_valid_to_semester_key": valid_to_key,
                "_is_current": False,
            })

            current_program_id = shift["to_program_id"]
            valid_from_key = sem_key[(int(shift["academic_year"]), int(shift["semester_number"]))]
            # college_id may or may not change with the shift -- to_program_id could be in a
            # different college; resolved by the caller joining against dim_program if needed.
            # Here we keep tracking via program_id's own college through the program dimension
            # rather than duplicating that lookup inline.

        all_rows.append({
            "student_id": sid, "gender": student["gender"], "birth_year": student["birth_year"],
            "home_province": student["home_province"], "admission_type": student["admission_type"],
            "college_id": current_college_id, "program_id": current_program_id,
            "_valid_from_semester_key": valid_from_key, "_valid_to_semester_key": None,
            "_is_current": True,
        })

    result = pd.DataFrame(all_rows).reset_index(drop=True)
    result.insert(0, "student_key", range(1, len(result) + 1))
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_all_dimensions(
    silver_storage: Optional[ObjectStorage] = None,
    gold_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, int]:
    silver_storage = silver_storage or LocalFileStorage(DEFAULT_SILVER_STORAGE_PATH)
    gold_storage = gold_storage or LocalFileStorage(DEFAULT_GOLD_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    college_df = _read_parquet(silver_storage, "silver/college/data.parquet")
    program_df = _read_parquet(silver_storage, "silver/program/data.parquet")
    student_df = _read_parquet(silver_storage, "silver/student/data.parquet")
    shifter_df = _read_parquet(silver_storage, "silver/shifter/data.parquet")

    dim_academic_year = build_dim_academic_year()
    dim_semester = build_dim_semester(dim_academic_year)
    dim_calendar = build_dim_calendar(dim_semester)
    dim_college = build_dim_college(college_df)
    dim_program = build_dim_program(program_df, dim_college)
    dim_student = build_dim_student(student_df, shifter_df, dim_semester)

    tables = {
        "dim_academic_year": dim_academic_year,
        "dim_semester": dim_semester,
        "dim_calendar": dim_calendar,
        "dim_college": dim_college,
        "dim_program": dim_program,
        "dim_student": dim_student,
    }
    for name, df in tables.items():
        _write_parquet(gold_storage, f"gold/{name}/data.parquet", df)

    row_counts = {name: len(df) for name, df in tables.items()}

    record_run(
        meta_conn, run_id, batch_id=run_id, stage=STAGE, entity="all_dimensions", partition_key="all",
        started_at=started_at, status="SUCCESS", rows_out=sum(row_counts.values()),
        source_path="silver/*", error_message=str(row_counts),
    )

    if owns_conn:
        meta_conn.close()

    return row_counts


if __name__ == "__main__":
    counts = build_all_dimensions()
    print("Gold dimension build complete:")
    for name, count in counts.items():
        print(f"  {name}: {count} rows")
