"""
pipelines/silver/business_rules.py

Task 22: "Schema validation is not enough." pipelines/common/
silver_schemas.py (Task 21) only ever looks at ONE row of ONE entity at
a time -- it cannot know whether a program's college_id actually exists,
or whether an enrollment row's college_id agrees with what the program
dimension itself says. That is what this module checks: relationships
BETWEEN rows and BETWEEN entities.

Every check function follows the same (valid_df, quarantined_df)
contract already established by pipelines/silver/validate_and_dedupe.py
and pipelines/silver/progression_validation.py, so run_business_validation
below composes them the same way validate_and_dedupe.process_enrollment
does: quarantine, don't silently drop, and log why.

Checks implemented, mapped onto this project's real 7 Silver entities:
  1. check_program_belongs_to_college -- program.college_id exists in
     the college dimension.
  2. check_program_college_consistency -- an enrollment/graduation/
     dropout row's (program_id, college_id) pair agrees with the
     program dimension's OWN registered college_id for that program.
  3. check_semester_valid -- semester_number is one of the actually
     modeled values (data-driven from academic_periods.SEMESTER_LABELS,
     not a hardcoded literal).
  4. check_academic_year_valid -- academic_year falls within this
     dataset's observed generation window.
  5. check_year_level_valid -- year_level is a positive integer that is
     structurally plausible for the STUDENT'S OWN program's nominal
     duration (not one global cutoff for every program).
  6. check_counts_non_negative -- generic non-negative check for any
     count-like numeric column.
  7. check_admissions_funnel -- accepted <= applicants and
     enrolled <= accepted. Implemented and unit-tested against the exact
     shape it's specified for, but NOT wired into run_business_validation
     below: no entity currently ingested through Bronze/Silver carries
     applicants/accepted/enrolled_freshmen together (see this function's
     own docstring for why, and how it activates automatically once that
     changes). Fabricating those columns onto today's 7 entities, which
     don't model an admissions funnel, would be worse than being explicit
     that the check doesn't apply yet.

Run via: python -m pipelines.silver.business_rules
"""

from __future__ import annotations

import io
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from pipelines.common.academic_periods import OBSERVED_START_YEAR, SEMESTER_LABELS
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage, ObjectStorage

# ADD near the top, after existing imports/constants
from pipelines.common.errors import (
    DataQualityFailureError,
    InvalidAcademicYearError,
    InvalidSchemaError,
    InvalidYearLevelError,
)

# Task 47: a quarantine rate above this on a single business-rule check
# means "the data is structurally broken", not "a normal sprinkling of
# bad rows" -- escalate to a hard, traceable failure instead of quietly
# reporting SUCCESS with almost everything discarded.
MAX_QUARANTINE_RATE = 0.25

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"

STAGE = "silver_business_validation"

# Data-driven, not magic numbers: this project's generator only ever
# produces 3 cohorts (2021-2023) and 2 semesters/year -- see
# data_generator/config/volumes.yaml and academic_periods.SEMESTER_LABELS.
DEFAULT_OBSERVED_ACADEMIC_YEARS: Tuple[int, ...] = tuple(range(OBSERVED_START_YEAR, OBSERVED_START_YEAR + 3))
DEFAULT_VALID_SEMESTER_NUMBERS: Tuple[int, ...] = tuple(range(1, len(SEMESTER_LABELS) + 1))

# Which count-like columns to non-negativity-check per entity (Task 22's
# "counts are non-negative").
COUNT_COLUMNS: Dict[str, List[str]] = {
    "enrollment": ["units_enrolled", "year_level"],
    "graduation": ["years_to_complete"],
    "dropout": ["semesters_completed_before_dropout"],
    "student": ["entry_year_level"],
}


# ---------------------------------------------------------------------------
# 1. program belongs to college
# ---------------------------------------------------------------------------

def check_program_belongs_to_college(
    program_df: pd.DataFrame, college_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """A program's college_id must reference a real row in the college
    dimension -- a program pointing at a college that doesn't exist is a
    broken foreign key, not a data-quality nuance."""
    valid_college_ids = set(college_df["college_id"])
    mask = program_df["college_id"].isin(valid_college_ids)
    valid = program_df[mask].copy()
    quarantined = program_df[~mask].copy()
    quarantined["_quarantine_reason"] = "college_id not found in Silver college dimension"
    return valid, quarantined


# ---------------------------------------------------------------------------
# 2. program-college consistency on fact rows
# ---------------------------------------------------------------------------

def check_program_college_consistency(
    df: pd.DataFrame, program_df: pd.DataFrame, entity_label: str
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """A fact row's (program_id, college_id) pair must match the
    program's OWN registered college_id -- catches denormalization drift
    where e.g. an enrollment row's college_id disagrees with the program
    dimension (Task 22's 'program belongs to college', applied at the
    fact-row level rather than within the program dimension itself)."""
    college_by_program = program_df.set_index("program_id")["college_id"].to_dict()

    def _is_consistent(row) -> bool:
        expected_college = college_by_program.get(row["program_id"])
        if expected_college is None:
            return False  # program_id isn't even in the program dimension
        return row["college_id"] == expected_college

    mask = df.apply(_is_consistent, axis=1)
    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = f"{entity_label}: college_id inconsistent with program's registered college"
    return valid, quarantined


# ---------------------------------------------------------------------------
# 3. semester is valid
# ---------------------------------------------------------------------------

def check_semester_valid(
    df: pd.DataFrame, valid_semesters: Tuple[int, ...] = DEFAULT_VALID_SEMESTER_NUMBERS
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    mask = df["semester_number"].isin(valid_semesters)
    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = f"semester_number not in {sorted(valid_semesters)}"
    return valid, quarantined


# ---------------------------------------------------------------------------
# 4. academic year is valid
# ---------------------------------------------------------------------------

def check_academic_year_valid(
    df: pd.DataFrame, observed_years: Tuple[int, ...] = DEFAULT_OBSERVED_ACADEMIC_YEARS
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    mask = df["academic_year"].isin(observed_years)
    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = f"academic_year outside observed window {observed_years}"
    return valid, quarantined


# ---------------------------------------------------------------------------
# 5. year level is valid
# ---------------------------------------------------------------------------

def check_year_level_valid(
    df: pd.DataFrame,
    program_df: pd.DataFrame,
    year_level_col: str = "year_level",
    program_id_col: str = "program_id",
    buffer_years: int = 2,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """year_level must be a positive integer that doesn't exceed what's
    structurally plausible for THIS ROW'S OWN program's nominal duration
    -- not one global cutoff shared by a 2-year certificate and a 5-year
    engineering degree. `buffer_years` extra years on top of
    ceil(nominal_duration_years) legitimately covers Super Senior
    students (pipelines/common/academic_periods.is_super_senior) without
    hardcoding an arbitrary cross-program cap.
    """
    duration_by_program = program_df.set_index("program_id")["nominal_duration_years"].to_dict()

    def _is_valid(row) -> bool:
        level = row[year_level_col]
        if pd.isna(level) or level < 1:
            return False
        duration = duration_by_program.get(row[program_id_col])
        if duration is None:
            return False  # unknown program -- can't establish plausibility
        return level <= math.ceil(duration) + buffer_years

    mask = df.apply(_is_valid, axis=1)
    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = (
        f"{year_level_col} outside plausible range for this program's nominal duration"
    )
    return valid, quarantined


# ---------------------------------------------------------------------------
# 6. counts are non-negative
# ---------------------------------------------------------------------------

def check_counts_non_negative(df: pd.DataFrame, columns: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Generic non-negative check for count-like numeric columns -- one
    reusable function so every entity with a "counts" column
    (units_enrolled, semesters_completed_before_dropout, ...) gets the
    same treatment instead of a bespoke check per column."""
    present = [c for c in columns if c in df.columns]
    if not present:
        return df, df.iloc[0:0].copy()

    mask = pd.Series(True, index=df.index)
    for col in present:
        mask &= df[col].isna() | (df[col] >= 0)

    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = f"negative value in one of {present}"
    return valid, quarantined


# ---------------------------------------------------------------------------
# 7. admissions funnel: accepted <= applicants, enrolled <= accepted
# ---------------------------------------------------------------------------

def check_admissions_funnel(
    df: pd.DataFrame,
    applicants_col: str = "applicants",
    accepted_col: str = "accepted",
    enrolled_col: str = "enrolled_freshmen",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """accepted <= applicants and enrolled <= accepted.

    NOTE: no entity flowing through Bronze/Silver today carries these
    three columns together. data_generator/generators/generate_admissions.py
    can produce an admissions funnel (output/{year}/admissions.csv:
    academic_year, college_id, program_id, applicants, accepted,
    enrolled_freshmen), but it was never added to
    pipelines/ingestion/ingest_to_bronze.py's REQUIRED_COLUMNS, so there
    is no Bronze/Silver 'admissions' entity yet to validate -- adding one
    is ingestion-layer work, out of this Silver task's scope. This
    function is implemented and unit-tested against the funnel shape it's
    specified for; run_business_validation() below does NOT call it
    against today's 7 entities (none of them have this relationship), so
    that fabricated columns are never invented just to satisfy this
    check. The moment an 'admissions' entity exists in Silver, wiring
    this function in is a one-line addition to run_business_validation.
    """
    missing = [c for c in (applicants_col, accepted_col, enrolled_col) if c not in df.columns]
    if missing:
        raise InvalidSchemaError(
            f"check_admissions_funnel requires columns {[applicants_col, accepted_col, enrolled_col]}; "
            f"missing {missing}. See this function's docstring: today's 7 Silver entities do not "
            f"model an admissions funnel.",
            stage="Silver Business Validation", rows_affected=len(df), details={"missing_columns": missing},
        )
    mask = (df[accepted_col] <= df[applicants_col]) & (df[enrolled_col] <= df[accepted_col])
    valid = df[mask].copy()
    quarantined = df[~mask].copy()
    quarantined["_quarantine_reason"] = "admissions funnel violated: accepted<=applicants or enrolled<=accepted"
    return valid, quarantined


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def _record_and_write(
    storage: ObjectStorage, meta_conn, entity: str, original_df: pd.DataFrame,
    valid_df: pd.DataFrame, quarantined_df: pd.DataFrame,
) -> Dict[str, object]:
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    _write_parquet(storage, f"silver/{entity}/data.parquet", valid_df)
    if len(quarantined_df) > 0:
        _write_parquet(storage, f"silver_quarantine/{entity}/business_rules.parquet", quarantined_df)

    rows_in = len(original_df)
    quarantine_rate = len(quarantined_df) / rows_in if rows_in else 0.0
    record_run(
        meta_conn, run_id, batch_id=run_id, stage=STAGE, entity=entity, partition_key="all",
        started_at=started_at, status="SUCCESS", rows_in=rows_in, rows_out=len(valid_df),
        source_path=f"silver/{entity}",
        error_message=f"quarantined={len(quarantined_df)} ({quarantine_rate:.2%})",
    )
    return {
        "rows_in": rows_in, "rows_out": len(valid_df),
        "quarantined": len(quarantined_df), "quarantine_rate": quarantine_rate,
    }


def run_business_validation(
    silver_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, Dict[str, object]]:
    """Run every applicable Task 22 check against the current Silver
    tables, overwrite each entity's Silver Parquet with only the valid
    rows, and write violating rows to silver_quarantine/<entity>/
    business_rules.parquet (matching validate_and_dedupe.py's existing
    quarantine-not-drop convention). Returns a per-entity summary.
    """
    silver_storage = silver_storage or LocalFileStorage(DEFAULT_SILVER_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    college_df = _read_parquet(silver_storage, "silver/college/data.parquet")
    program_df = _read_parquet(silver_storage, "silver/program/data.parquet")

    results: Dict[str, Dict[str, object]] = {}

    # --- program: belongs to college ---
    valid_program, bad_program = check_program_belongs_to_college(program_df, college_df)
    results["program"] = _record_and_write(silver_storage, meta_conn, "program", program_df, valid_program, bad_program)

    # --- fact entities: program-college consistency + semester + academic year + counts ---
    for entity in ("enrollment", "graduation", "dropout"):
        try:
            df = _read_parquet(silver_storage, f"silver/{entity}/data.parquet")
        except FileNotFoundError:
            continue

        # AFTER
        rows_in = len(df)
        working, bad_college = check_program_college_consistency(df, program_df, entity)
        working, bad_semester = check_semester_valid(working)
        _enforce_quality_gate(entity, "check_semester_valid", bad_semester, rows_in,
                               InvalidSchemaError, "Silver Business Validation")  # not a listed category on its own; folded under schema

        working, bad_year = check_academic_year_valid(working)
        _enforce_quality_gate(entity, "check_academic_year_valid", bad_year, rows_in,
                               InvalidAcademicYearError, "Silver Business Validation")

        working, bad_counts = check_counts_non_negative(working, COUNT_COLUMNS.get(entity, []))
        if entity == "enrollment":
            working, bad_year_level = check_year_level_valid(working, program_df)
            _enforce_quality_gate(entity, "check_year_level_valid", bad_year_level, rows_in,
                                   InvalidYearLevelError, "Silver Business Validation")
        else:
            bad_year_level = working.iloc[0:0].copy()

        all_bad = pd.concat(
            [bad_college, bad_semester, bad_year, bad_counts, bad_year_level], ignore_index=True
        )
        results[entity] = _record_and_write(silver_storage, meta_conn, entity, df, working, all_bad)

    # --- shifter: models two programs, not one college -- semester/academic-year only ---
    try:
        shifter_df = _read_parquet(silver_storage, "silver/shifter/data.parquet")
        working, bad_semester = check_semester_valid(shifter_df)
        working, bad_year = check_academic_year_valid(working)
        all_bad = pd.concat([bad_semester, bad_year], ignore_index=True)
        results["shifter"] = _record_and_write(silver_storage, meta_conn, "shifter", shifter_df, working, all_bad)
    except FileNotFoundError:
        pass

    if owns_conn:
        meta_conn.close()
    return results

# ADD new helper, near _record_and_write

def _enforce_quality_gate(
    entity: str, check_name: str, bad_df: pd.DataFrame, rows_in: int, error_cls, stage: str,
) -> None:
    """Task 47: escalate a single check's quarantine rate to a
    categorized, traceable hard failure when it crosses
    MAX_QUARANTINE_RATE, instead of reporting SUCCESS regardless of how
    much data that one check discarded."""
    if rows_in == 0 or bad_df.empty:
        return
    rate = len(bad_df) / rows_in
    if rate > MAX_QUARANTINE_RATE:
        raise error_cls(
            f"{check_name} quarantined {len(bad_df)}/{rows_in} rows ({rate:.1%}) for entity {entity!r} "
            f"-- exceeds the {MAX_QUARANTINE_RATE:.0%} tolerance for this check.",
            stage=stage, entity=entity, rows_affected=len(bad_df),
        )


if __name__ == "__main__":
    summary = run_business_validation()
    print("Silver business validation complete:")
    for entity, stats in summary.items():
        print(f"  {entity}: rows_in={stats['rows_in']}, rows_out={stats['rows_out']}, "
              f"quarantined={stats['quarantined']} ({stats['quarantine_rate']:.2%})")