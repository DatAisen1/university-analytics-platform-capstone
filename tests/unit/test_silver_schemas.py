"""
tests/unit/test_silver_schemas.py

Tests for pipelines/common/silver_schemas.py: validates the Silver
(post-cleaning) shape using the SAME nullable dtypes
pipelines/silver/clean_entities.py's TARGET_DTYPES actually produces.
Building fixtures with plain python values and casting them via
TARGET_DTYPES keeps this test honest about what real cleaned output
looks like, instead of accidentally testing against numpy's default
dtype inference (which would silently diverge from what clean_entities.py
writes to Silver).
"""

import pandas as pd
import pandera.errors
import pytest

from pipelines.common.silver_schemas import get_silver_schema, validate_silver_dataframe
from pipelines.silver.clean_entities import TARGET_DTYPES

VALID_ROWS = {
    "college": {"college_id": "COA", "college_name": "College of Architecture"},
    "program": {"program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
                "program_level": "Bachelor", "nominal_duration_years": 5.0},
    "student": {"student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male",
                "birth_year": 2003, "home_province": "Nueva Ecija", "admission_type": "Freshman",
                "entry_year_level": 1, "entry_college_id": "COA", "entry_program_id": "COA-BSARCH"},
    "enrollment": {"student_id": "2021-00001", "academic_year": 2021, "semester_number": 1,
                   "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "ENROLLED",
                   "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
    "graduation": {"student_id": "2021-00001", "academic_year": 2024, "semester_number": 2,
                   "program_id": "COA-BSARCH", "college_id": "COA", "years_to_complete": 4.0},
    "dropout": {"student_id": "2021-00001", "academic_year": 2022, "semester_number": 1,
                "program_id": "COA-BSARCH", "college_id": "COA", "dropout_reason": "Financial",
                "semesters_completed_before_dropout": 2},
    "shifter": {"student_id": "2021-00001", "academic_year": 2021, "semester_number": 2,
                "from_program_id": "COA-BSARCH", "to_program_id": "CICT-BSDS"},
}


def _as_silver_frame(entity, rows):
    df = pd.DataFrame(rows)
    for col, dtype in TARGET_DTYPES[entity].items():
        if col in df.columns:
            df[col] = df[col].astype(dtype)
    return df


@pytest.mark.parametrize("entity", list(VALID_ROWS.keys()))
def test_valid_row_passes_silver_schema(entity):
    df = _as_silver_frame(entity, [VALID_ROWS[entity]])
    validate_silver_dataframe(df, entity)  # should not raise


def test_get_silver_schema_unknown_entity_raises_key_error():
    with pytest.raises(KeyError, match="No Silver schema defined"):
        get_silver_schema("not_a_real_entity")


def test_student_invalid_gender_is_caught():
    """An 'UNKNOWN:<raw>' tag -- exactly what cleaning produces for an
    unmappable value -- must fail Silver's schema, unlike Bronze's
    deliberately permissive enrollment_status column."""
    row = dict(VALID_ROWS["student"])
    row["gender"] = "UNKNOWN:Other"
    df = _as_silver_frame("student", [row])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_silver_dataframe(df, "student")


def test_enrollment_academic_year_outside_observed_window_is_caught():
    row = dict(VALID_ROWS["enrollment"])
    row["academic_year"] = 2019
    df = _as_silver_frame("enrollment", [row])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_silver_dataframe(df, "enrollment")


def test_enrollment_negative_units_is_caught():
    row = dict(VALID_ROWS["enrollment"])
    row["units_enrolled"] = -5
    df = _as_silver_frame("enrollment", [row])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_silver_dataframe(df, "enrollment")


def test_dropout_negative_semesters_completed_is_caught():
    row = dict(VALID_ROWS["dropout"])
    row["semesters_completed_before_dropout"] = -1
    df = _as_silver_frame("dropout", [row])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_silver_dataframe(df, "dropout")


def test_program_invalid_level_is_caught():
    row = dict(VALID_ROWS["program"])
    row["program_level"] = "Postgraduate"
    df = _as_silver_frame("program", [row])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_silver_dataframe(df, "program")