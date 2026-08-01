"""
tests/unit/test_business_rules.py

Tests for pipelines/silver/business_rules.py (Task 22): each check
function in isolation, plus an integration test proving
run_business_validation composes them correctly against real Silver
Parquet files and quarantines (never silently drops) violating rows.
"""

import io

import pandas as pd
import pytest

from pipelines.common.metadata import get_connection
from pipelines.common.storage import LocalFileStorage
from pipelines.silver.business_rules import (
    check_academic_year_valid,
    check_admissions_funnel,
    check_counts_non_negative,
    check_program_belongs_to_college,
    check_program_college_consistency,
    check_semester_valid,
    check_year_level_valid,
    run_business_validation,
)


# ---------------------------------------------------------------------------
# 1. program belongs to college
# ---------------------------------------------------------------------------

def test_check_program_belongs_to_college_flags_dangling_reference():
    college_df = pd.DataFrame([{"college_id": "COA", "college_name": "College of Architecture"}])
    program_df = pd.DataFrame([
        {"program_id": "COA-BSARCH", "college_id": "COA"},
        {"program_id": "GHOST-PROG", "college_id": "DOES-NOT-EXIST"},
    ])
    valid, quarantined = check_program_belongs_to_college(program_df, college_df)
    assert list(valid["program_id"]) == ["COA-BSARCH"]
    assert list(quarantined["program_id"]) == ["GHOST-PROG"]


# ---------------------------------------------------------------------------
# 2. program-college consistency
# ---------------------------------------------------------------------------

def test_check_program_college_consistency_flags_drift():
    program_df = pd.DataFrame([{"program_id": "COA-BSARCH", "college_id": "COA"}])
    enrollment_df = pd.DataFrame([
        {"student_id": "s1", "program_id": "COA-BSARCH", "college_id": "COA"},       # consistent
        {"student_id": "s2", "program_id": "COA-BSARCH", "college_id": "WRONG-COL"},  # drifted
    ])
    valid, quarantined = check_program_college_consistency(enrollment_df, program_df, "enrollment")
    assert list(valid["student_id"]) == ["s1"]
    assert list(quarantined["student_id"]) == ["s2"]


# ---------------------------------------------------------------------------
# 3. semester is valid
# ---------------------------------------------------------------------------

def test_check_semester_valid_flags_out_of_range_semester():
    df = pd.DataFrame([{"semester_number": 1}, {"semester_number": 3}])
    valid, quarantined = check_semester_valid(df)
    assert len(valid) == 1 and len(quarantined) == 1


# ---------------------------------------------------------------------------
# 4. academic year is valid
# ---------------------------------------------------------------------------

def test_check_academic_year_valid_flags_out_of_window_year():
    df = pd.DataFrame([{"academic_year": 2022}, {"academic_year": 1999}])
    valid, quarantined = check_academic_year_valid(df)
    assert len(valid) == 1 and len(quarantined) == 1


# ---------------------------------------------------------------------------
# 5. year level is valid
# ---------------------------------------------------------------------------

def test_check_year_level_valid_uses_program_specific_duration():
    program_df = pd.DataFrame([
        {"program_id": "SHORT-CERT", "nominal_duration_years": 1.0},   # cap = ceil(1)+2 = 3
        {"program_id": "LONG-ENGR", "nominal_duration_years": 5.0},    # cap = ceil(5)+2 = 7
    ])
    df = pd.DataFrame([
        {"program_id": "SHORT-CERT", "year_level": 3},   # plausible for a 1-year cert
        {"program_id": "SHORT-CERT", "year_level": 6},   # implausible for a 1-year cert
        {"program_id": "LONG-ENGR", "year_level": 6},    # plausible for a 5-year degree
        {"program_id": "UNKNOWN-PROG", "year_level": 1}, # unresolvable program
    ])
    valid, quarantined = check_year_level_valid(df, program_df)
    assert set(zip(valid["program_id"], valid["year_level"])) == {("SHORT-CERT", 3), ("LONG-ENGR", 6)}
    assert len(quarantined) == 2


def test_check_year_level_valid_rejects_zero_or_negative():
    program_df = pd.DataFrame([{"program_id": "P1", "nominal_duration_years": 4.0}])
    df = pd.DataFrame([{"program_id": "P1", "year_level": 0}])
    valid, quarantined = check_year_level_valid(df, program_df)
    assert valid.empty and len(quarantined) == 1


# ---------------------------------------------------------------------------
# 6. counts are non-negative
# ---------------------------------------------------------------------------

def test_check_counts_non_negative_flags_negative_values():
    df = pd.DataFrame([{"units_enrolled": 18}, {"units_enrolled": -3}])
    valid, quarantined = check_counts_non_negative(df, ["units_enrolled"])
    assert len(valid) == 1 and len(quarantined) == 1


def test_check_counts_non_negative_no_matching_columns_is_a_no_op():
    df = pd.DataFrame([{"unrelated": 1}])
    valid, quarantined = check_counts_non_negative(df, ["units_enrolled"])
    assert len(valid) == 1 and quarantined.empty


# ---------------------------------------------------------------------------
# 7. admissions funnel (documented as currently inapplicable to this
# dataset's 7 Silver entities -- tested against the shape it's specified for)
# ---------------------------------------------------------------------------

def test_check_admissions_funnel_flags_broken_monotonicity():
    df = pd.DataFrame([
        {"applicants": 100, "accepted": 60, "enrolled_freshmen": 40},   # valid: 40<=60<=100
        {"applicants": 100, "accepted": 120, "enrolled_freshmen": 40},  # invalid: accepted>applicants
        {"applicants": 100, "accepted": 60, "enrolled_freshmen": 90},   # invalid: enrolled>accepted
    ])
    valid, quarantined = check_admissions_funnel(df)
    assert len(valid) == 1 and len(quarantined) == 2


def test_check_admissions_funnel_missing_columns_raises_key_error():
    """No current Silver entity carries applicants/accepted/enrolled_
    freshmen together -- calling this against one of today's 7 entities
    must fail loudly, not silently pass everything."""
    df = pd.DataFrame([{"student_id": "s1", "units_enrolled": 18}])
    with pytest.raises(KeyError, match="does not model an admissions funnel"):
        check_admissions_funnel(df)


# ---------------------------------------------------------------------------
# Integration: run_business_validation against real Silver Parquet files
# ---------------------------------------------------------------------------

def _write_silver_parquet(storage, key, df):
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def test_run_business_validation_quarantines_and_reports(tmp_path):
    storage = LocalFileStorage(tmp_path / "silver_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    _write_silver_parquet(storage, "silver/college/data.parquet", pd.DataFrame([
        {"college_id": "COA", "college_name": "College of Architecture"},
    ]))
    _write_silver_parquet(storage, "silver/program/data.parquet", pd.DataFrame([
        {"program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
         "nominal_duration_years": 5.0},
        {"program_id": "GHOST-PROG", "program_name": "Ghost Program", "college_id": "NOPE",
         "nominal_duration_years": 4.0},
    ]))
    _write_silver_parquet(storage, "silver/enrollment/data.parquet", pd.DataFrame([
        # valid row
        {"student_id": "s1", "academic_year": 2021, "semester_number": 1,
         "program_id": "COA-BSARCH", "college_id": "COA", "year_level": 1, "units_enrolled": 18},
        # invalid: negative units_enrolled
        {"student_id": "s2", "academic_year": 2021, "semester_number": 1,
         "program_id": "COA-BSARCH", "college_id": "COA", "year_level": 1, "units_enrolled": -5},
        # invalid: semester_number out of range
        {"student_id": "s3", "academic_year": 2021, "semester_number": 9,
         "program_id": "COA-BSARCH", "college_id": "COA", "year_level": 1, "units_enrolled": 18},
    ]))

    results = run_business_validation(silver_storage=storage, meta_conn=meta_conn)

    assert results["program"]["quarantined"] == 1   # GHOST-PROG
    assert results["enrollment"]["quarantined"] == 2  # s2, s3
    assert results["enrollment"]["rows_out"] == 1

    quarantined_enrollment = pd.read_parquet(
        io.BytesIO(storage.read_bytes("silver_quarantine/enrollment/business_rules.parquet"))
    )
    assert set(quarantined_enrollment["student_id"]) == {"s2", "s3"}

    surviving_program = pd.read_parquet(io.BytesIO(storage.read_bytes("silver/program/data.parquet")))
    assert list(surviving_program["program_id"]) == ["COA-BSARCH"]