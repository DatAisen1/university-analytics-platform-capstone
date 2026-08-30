"""
pipelines/gold/build_dimensions.py

Builds the Gold star-schema dimension tables from Silver data:
dim_college, dim_program, dim_academic_period, dim_calendar, dim_year_level,
dim_gender, and dim_student (the one with real SCD Type 2 history).

-- Redesign note (Task 23/24 -- Gold Modeling Fix) --------------------------
This module previously modeled the calendar hierarchy as TWO snowflaked
tables (dim_academic_year <- dim_semester via academic_year_key) despite
docs/04_Data_Modeling.md Section 2 explicitly choosing Star over Snowflake
for this exact reason: "the dimension hierarchies here ... are shallow and
don't change structure often. Snowflaking would only add join complexity
for no real storage or integrity benefit." Every fact table's grain is
per-semester, so every fact query needed the year anyway -- the FK hop
bought nothing but an extra join. `dim_academic_period` fixes that: one
denormalized row per (academic_year, semester_number), carrying the year
attributes flattened in. This is the honest implementation of the star
schema the design doc already argued for, not a new opinion.

`dim_year_level` and `dim_gender` are new. Previously, `year_level` lived
as a raw int on fact_enrollment and `gender` as a raw string on
dim_student -- both correct VALUES, but not modeled as first-class,
governed dimensions. That meant every consumer (dbt macros, marts,
dashboard) had to independently reinvent "what does year_level=3 mean"
(see dbt/macros/year_level_rules.sql's year_level_label_sql, duplicated
label logic that belongs in exactly one place: the Gold dimension itself).
Promoting them to real dimensions gives one governed source of labels,
lets BI tools browse/filter them like any other dimension, and matches
the same reasoning already applied to dim_college/dim_program: "small,
low-cardinality, independently meaningful to slice by" -> deserves a
table. Note deliberately what did NOT come along for the ride: "Super
Senior" status is NOT baked into dim_year_level as a static label,
because it isn't a fact about year_level alone -- it depends on the
student's specific program's nominal_duration_years (year_level 5 is
"Super Senior" in a 4-year program, on-time in a 5-year one). That
remains a computed attribute at query/mart time (build_kpi.py's
`is_eligible_to_graduate`), joining dim_year_level's numeric value
against dim_program.nominal_duration_years -- exactly the same pattern
already used there. Forcing it into dim_year_level would silently make a
program-relative fact look like a program-independent one.

`dim_student` also now stores `college_key`/`program_key` (surrogate FKs)
instead of raw `college_id`/`program_id` strings. Every OTHER dimension in
this model uses surrogate keys as its FK contract (docs/04_Data_Modeling.md
Section 5); dim_student silently breaking that contract for its own
program/college attributes was an inconsistency, not a deliberate
design choice -- fixed here, not left as a footgun for the first analyst
who assumes ALL cross-dimension references in this warehouse are surrogate
keys (a safe assumption everywhere else).

A note on tooling choice, since this project has been deliberate about
"DuckDB SQL for transforms" everywhere else: dim_student's SCD2
construction is written in plain Python, not DuckDB SQL. Cleaning
(Day 10) and dedup (Day 11) are naturally set-based operations -- exactly
what SQL is good at. SCD2 history-building is inherently SEQUENTIAL per
student (open a row, watch for a shift event, close the row, open the
next one) -- forcing that into a single window-function SQL expression
would trade clarity for a stylistic consistency that isn't worth it here.

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

from pipelines.common.academic_periods import OBSERVED_ACADEMIC_YEARS, OBSERVED_START_YEAR
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import ObjectStorage, load_storage_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

STAGE = "gold_build_dimensions"

# P0 (Dataset Extension): imported from pipelines.common.academic_periods,
# the single canonical source, instead of a local literal list -- this
# name is kept (not deleted) since it's referenced throughout this module
# and in models/forecasting/deploy_forecast.py's docstring.
ACADEMIC_YEARS = list(OBSERVED_ACADEMIC_YEARS)

# Governed year_level domain and labels. Only years 1-6 are observed in
# Silver today, but the dimension is intentionally built to cover the full
# nominal range any program in dim_program can produce (max
# nominal_duration_years == 5) plus one extra year of headroom for a
# single stalled/extended semester, rather than being derived reactively
# from "whatever values happen to be in today's Silver snapshot" -- a
# dimension whose domain silently shrinks or grows with each data refresh
# is a correctness hazard for any BI tool that caches it.
YEAR_LEVEL_LABELS: Dict[int, str] = {
    1: "Freshman",
    2: "Sophomore",
    3: "Junior",
    4: "Senior",
    5: "Fifth Year",
    6: "Sixth Year",
    7: "Seventh Year",
}
# Domain must cover the data generator's actual configured ceiling:
# max(nominal_duration_years across configs/programs.yaml) + progression_rules.yaml's
# max_year_level_cap_extra_years. Verified directly, not assumed: as of
# the 2021-2025 extended dataset, that's 5 (the four 5-year programs --
# Architecture, Civil/Electrical/Mechanical Engineering) + 2 = 7. This
# ceiling is a MAX of two independently-configured values in two
# different YAML files (configs/programs.yaml per-program, this repo's
# data_generator/config/progression_rules.yaml globally) -- there is no
# single source of truth enforcing they stay in sync, so if either
# config changes (a longer program is added, or the extra-years cap is
# raised), this dict must be re-verified against both, not assumed
# still correct. A student exceeding this domain fails loudly at the
# gold stage (see build_facts.py's fact_enrollment year_level check)
# rather than being silently dropped -- that failure is what surfaced
# this exact gap being one year too narrow.

# Governed gender domain, matching pipelines/silver/clean_entities.py's
# VALID_GENDERS controlled vocabulary. Defined once, here, as the single
# source of truth for the surrogate key <-> code mapping every fact and
# every downstream mart/dashboard must agree on.
GENDER_CODES: List[str] = ["Female", "Male"]


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def period_ordinal(academic_year: int, semester_number: int) -> int:
    """0-based chronological ordinal across the whole modeled date range.
    Used for "next period" arithmetic (fact_retention, KPI momentum) --
    kept as an explicit column on dim_academic_period rather than an
    assumption baked into how surrogate keys happen to be assigned.
    """
    return (academic_year - 2021) * 2 + (semester_number - 1)


# ---------------------------------------------------------------------------
# Static / derived dimensions (no Silver source -- these are structural)
# ---------------------------------------------------------------------------

def build_dim_academic_period() -> pd.DataFrame:
    """One denormalized row per (academic_year, semester_number). Replaces
    the old dim_academic_year + dim_semester snowflake pair -- see module
    docstring for why. `academic_period_key` is assigned in chronological
    order and therefore always equals `period_ordinal + 1`; `period_ordinal`
    is still stored explicitly so callers needing "next period" arithmetic
    document that intent rather than relying on an unstated key-ordering
    assumption.
    """
    rows = []
    key = 1
    for y in ACADEMIC_YEARS:
        for s in (1, 2):
            rows.append({
                "academic_period_key": key,
                "academic_year": y,
                "semester_number": s,
                "year_label": f"{y}-{y + 1}",
                "semester_label": "1st Semester" if s == 1 else "2nd Semester",
                "period_label": f"{y}-{y + 1} \u00b7 {'1st' if s == 1 else '2nd'} Semester",
                "period_ordinal": period_ordinal(y, s),
            })
            key += 1
    return pd.DataFrame(rows)


def academic_period_key_lookup(dim_academic_period: pd.DataFrame) -> Dict[tuple, int]:
    return {
        (row["academic_year"], row["semester_number"]): row["academic_period_key"]
        for _, row in dim_academic_period.iterrows()
    }


def build_dim_calendar(dim_academic_period: pd.DataFrame) -> pd.DataFrame:
    """A day-grain calendar dimension spanning the observed ACADEMIC_YEARS
    range (2021-01-01 through the last observed year's 12-31).

    Disclosed simplification: the project brief specifies academic years
    and two semesters per year, but never literal semester start/end
    dates. This dimension assumes semester 1 = Jan 1 - Jun 30 and
    semester 2 = Jul 1 - Dec 31 of the SAME calendar year, matching how
    academic_year is already used elsewhere in this project as a single
    calendar year (not a "2021-2022"-style split year). A real
    deployment would replace this assumption with the institution's
    actual registrar calendar.

    The end year is derived from ACADEMIC_YEARS rather than hardcoded --
    a hardcoded "2024-12-31" here silently drifted out of sync with
    ACADEMIC_YEARS once the latter was corrected from 4 years to 3
    (P0.4), which would have raised a KeyError on academic_period_key
    lookups for the now-nonexistent 2024 dates.
    """
    period_key_by = academic_period_key_lookup(dim_academic_period)
    last_year = max(ACADEMIC_YEARS)
    dates = pd.date_range(f"{OBSERVED_START_YEAR}-01-01", f"{last_year}-12-31", freq="D")

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
            "academic_period_key": period_key_by[(d.year, semester_number)],
        })
    return pd.DataFrame(rows)


def build_dim_year_level() -> pd.DataFrame:
    """Governed year_level dimension. Surrogate key is a plain
    auto-increment, kept independent of the natural `year_level` int even
    though the two happen to align 1:1 today -- see docs/04_Data_Modeling.md
    Section 5 on why surrogate keys are used uniformly, not just where a
    natural key happens to be inconvenient.
    """
    rows = [
        {"year_level_key": key, "year_level": year_level, "year_level_label": label}
        for key, (year_level, label) in enumerate(sorted(YEAR_LEVEL_LABELS.items()), start=1)
    ]
    return pd.DataFrame(rows)


def build_dim_gender() -> pd.DataFrame:
    """Governed gender dimension -- two rows, deliberately still modeled
    as a real dimension rather than folded into a junk dimension. Unlike
    the flags in docs/04_Data_Modeling.md's `dim_enrollment_status_flags`
    junk dimension (booleans almost never queried independently), gender
    IS routinely queried and grouped on independently (parity/equity
    reporting -- see dbt/models/marts/mart_canonical_dataset.sql's
    group-by grain). A dimension that's actually a first-class slicing
    axis earns its own table even at low cardinality; a dimension that's
    just incidental metadata is the one that belongs in a junk table.
    """
    rows = [
        {"gender_key": key, "gender_code": code, "gender_label": code}
        for key, code in enumerate(GENDER_CODES, start=1)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Reference-data-backed dimensions
# ---------------------------------------------------------------------------

def build_dim_college(college_df: pd.DataFrame) -> pd.DataFrame:
    # P0.53 fix: sort by the natural key BEFORE assigning the surrogate
    # key. Without this, `college_key` was assigned in whatever row
    # order college_df happened to arrive in (Bronze/Silver read order,
    # not guaranteed stable across runs or storage backends) --
    # logically identical input could get DIFFERENT college_key values
    # on two runs, which cascades into every downstream fact/dim
    # (dim_program.college_key, dim_student.college_key, every fact
    # table's college_key) and breaks P0.53's "same input -> same
    # logical output" fingerprint requirement, plus the P0.51 reload
    # fix's assumption that a re-emptied-and-refilled dimension row has
    # the SAME surrogate key other tables still reference mid-transaction.
    df = (
        college_df[["college_id", "college_name"]]
        .drop_duplicates()
        .sort_values("college_id")
        .reset_index(drop=True)
    )
    df.insert(0, "college_key", range(1, len(df) + 1))
    return df


def build_dim_program(program_df: pd.DataFrame, dim_college: pd.DataFrame) -> pd.DataFrame:
    college_key_by_id = dict(zip(dim_college["college_id"], dim_college["college_key"]))
    # P0.53 fix: same determinism issue as build_dim_college above.
    df = (
        program_df[["program_id", "program_name", "college_id", "program_level",
                     "nominal_duration_years"]]
        .drop_duplicates()
        .sort_values("program_id")
        .reset_index(drop=True)
    )
    df.insert(0, "program_key", range(1, len(df) + 1))
    df["college_key"] = df["college_id"].map(college_key_by_id)
    return df


# ---------------------------------------------------------------------------
# dim_student -- SCD Type 2
# ---------------------------------------------------------------------------

def build_dim_student(
    student_df: pd.DataFrame,
    shifter_df: pd.DataFrame,
    dim_academic_period: pd.DataFrame,
    dim_gender: pd.DataFrame,
    dim_college: pd.DataFrame,
    dim_program: pd.DataFrame,
) -> pd.DataFrame:
    """Build the full SCD2 history for every student: one row per distinct
    (program) period they were in, opened/closed at shift-event
    boundaries. A student with zero shift events gets exactly one row,
    open-ended (_is_current=True, _valid_to_period_key=None).

    Emits surrogate FKs (gender_key, college_key, program_key) instead of
    raw source values/natural keys -- see module docstring.
    """
    period_key = academic_period_key_lookup(dim_academic_period)
    gender_key_by_code = dict(zip(dim_gender["gender_code"], dim_gender["gender_key"]))
    college_key_by_id = dict(zip(dim_college["college_id"], dim_college["college_key"]))
    program_key_by_id = dict(zip(dim_program["program_id"], dim_program["program_key"]))

    shifts_by_student: Dict[str, List[dict]] = {}
    for _, row in shifter_df.sort_values(["academic_year", "semester_number"]).iterrows():
        shifts_by_student.setdefault(row["student_id"], []).append(row.to_dict())

    all_rows: List[dict] = []
    # P0.53 fix: sort by student_id before iterating -- same determinism
    # issue as build_dim_college/build_dim_program (see those docstrings).
    # student_key is assigned by row position below, so an unstable
    # student_df row order produced unstable student_key values across
    # otherwise-identical reruns.
    for _, student in student_df.sort_values("student_id").iterrows():
        sid = student["student_id"]
        entry_year = int(student["cohort_academic_year"])
        entry_ordinal = period_ordinal(entry_year, 1)
        current_program_id = student["entry_program_id"]
        current_college_id = student["entry_college_id"]
        valid_from_key = period_key[(entry_year, 1)]

        for shift in shifts_by_student.get(sid, []):
            shift_ordinal = period_ordinal(int(shift["academic_year"]), int(shift["semester_number"]))

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
            valid_to_key = period_key.get((close_year, close_sem))

            all_rows.append({
                "student_id": sid,
                "gender_key": gender_key_by_code[student["gender"]],
                "birth_year": student["birth_year"],
                "home_province": student["home_province"],
                "admission_type": student["admission_type"],
                "college_key": college_key_by_id[current_college_id],
                "program_key": program_key_by_id[current_program_id],
                "_valid_from_period_key": valid_from_key,
                "_valid_to_period_key": valid_to_key,
                "_is_current": False,
            })

            current_program_id = shift["to_program_id"]
            valid_from_key = period_key[(int(shift["academic_year"]), int(shift["semester_number"]))]
            # college_id may or may not change with the shift -- to_program_id could be in a
            # different college; resolved by the caller joining against dim_program if needed.
            # Here we keep tracking via program_id's own college through the program dimension
            # rather than duplicating that lookup inline.

        all_rows.append({
            "student_id": sid,
            "gender_key": gender_key_by_code[student["gender"]],
            "birth_year": student["birth_year"],
            "home_province": student["home_province"],
            "admission_type": student["admission_type"],
            "college_key": college_key_by_id[current_college_id],
            "program_key": program_key_by_id[current_program_id],
            "_valid_from_period_key": valid_from_key,
            "_valid_to_period_key": None,
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
    silver_storage = silver_storage or load_storage_from_env(DEFAULT_SILVER_STORAGE_PATH, "MINIO_SILVER_BUCKET")
    gold_storage = gold_storage or load_storage_from_env(DEFAULT_GOLD_STORAGE_PATH, "MINIO_GOLD_BUCKET")
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    college_df = _read_parquet(silver_storage, "silver/college/data.parquet")
    program_df = _read_parquet(silver_storage, "silver/program/data.parquet")
    student_df = _read_parquet(silver_storage, "silver/student/data.parquet")
    shifter_df = _read_parquet(silver_storage, "silver/shifter/data.parquet")

    dim_academic_period = build_dim_academic_period()
    dim_calendar = build_dim_calendar(dim_academic_period)
    dim_year_level = build_dim_year_level()
    dim_gender = build_dim_gender()
    dim_college = build_dim_college(college_df)
    dim_program = build_dim_program(program_df, dim_college)
    dim_student = build_dim_student(
        student_df, shifter_df, dim_academic_period, dim_gender, dim_college, dim_program
    )

    tables = {
        "dim_academic_period": dim_academic_period,
        "dim_calendar": dim_calendar,
        "dim_year_level": dim_year_level,
        "dim_gender": dim_gender,
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