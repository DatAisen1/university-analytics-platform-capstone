"""
tests/unit/test_schemas.py

Tests for pipelines/common/schemas.py: each entity's schema accepts its
own valid fixture and rejects a deliberately malformed variant of it --
plus the specific claim that Day 6's noisy-but-valid enrollment_status
text must NOT be rejected (Bronze validates shape, not cleanliness).
"""

import pandas as pd
import pandera.errors
import pytest

from pipelines.common.schemas import get_schema, validate_bronze_dataframe


VALID_ROWS = {
    "college": {"college_id": "COA", "college_name": "College of Architecture"},
    "program": {"program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
                "program_level": "Bachelor", "nominal_duration_years": 5.0},
    "student": {"student_id": "2021-00001", "cohort_academic_year": "2021-2022", "gender": "Male",
                "birth_year": 2003, "home_province": "Nueva Ecija", "admission_type": "Freshman",
                "entry_year_level": 1, "entry_college_id": "COA", "entry_program_id": "COA-BSARCH"},
    "enrollment": {"student_id": "2021-00001", "academic_year": "2021-2022", "semester_number": 1,
                   "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "ENROLLED",
                   "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
    "graduation": {"student_id": "2021-00001", "academic_year": "2024-2025", "semester_number": 2,
                   "program_id": "COA-BSARCH", "college_id": "COA", "years_to_complete": 4.0},
    "dropout": {"student_id": "2021-00001", "academic_year": "2022-2023", "semester_number": 1,
                "program_id": "COA-BSARCH", "college_id": "COA", "dropout_reason": "Financial",
                "semesters_completed_before_dropout": 2},
    "shifter": {"student_id": "2021-00001", "academic_year": "2021-2022", "semester_number": 2,
                "from_program_id": "COA-BSARCH", "to_program_id": "CICT-BSDS"},
}


@pytest.mark.parametrize("entity", list(VALID_ROWS.keys()))
def test_valid_row_passes_schema(entity):
    df = pd.DataFrame([VALID_ROWS[entity]])
    validate_bronze_dataframe(df, entity)  # should not raise


def test_get_schema_unknown_entity_raises_key_error():
    with pytest.raises(KeyError, match="No Bronze schema defined"):
        get_schema("not_a_real_entity")


# ---------------------------------------------------------------------------
# Deliberately malformed rows -- each entity, one real violation
# ---------------------------------------------------------------------------

def test_college_null_college_id_is_caught():
    df = pd.DataFrame([{"college_id": None, "college_name": "College of Architecture"}])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(df, "college")


def test_program_invalid_level_is_caught():
    row = dict(VALID_ROWS["program"])
    row["program_level"] = "Postgraduate"
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "program")


def test_program_out_of_range_duration_is_caught():
    row = dict(VALID_ROWS["program"])
    row["nominal_duration_years"] = 99.0
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "program")


def test_student_birth_year_out_of_range_is_caught():
    row = dict(VALID_ROWS["student"])
    row["birth_year"] = 1850
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "student")


def test_student_duplicate_student_id_is_caught():
    df = pd.DataFrame([VALID_ROWS["student"], VALID_ROWS["student"]])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(df, "student")


def test_enrollment_invalid_semester_number_is_caught():
    row = dict(VALID_ROWS["enrollment"])
    row["semester_number"] = 3  # only 1 or 2 are valid
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "enrollment")


def test_enrollment_negative_units_is_caught():
    row = dict(VALID_ROWS["enrollment"])
    row["units_enrolled"] = -5
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "enrollment")


def test_dropout_negative_semesters_completed_is_caught():
    row = dict(VALID_ROWS["dropout"])
    row["semesters_completed_before_dropout"] = -1
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "dropout")


def test_multiple_violations_are_all_reported_together():
    """lazy=True should collect every violation in one pass, not stop at
    the first -- important for a useful validation report."""
    row = dict(VALID_ROWS["program"])
    row["program_id"] = None
    row["program_level"] = "Postgraduate"
    row["nominal_duration_years"] = 99.0
    with pytest.raises(pandera.errors.SchemaErrors) as exc_info:
        validate_bronze_dataframe(pd.DataFrame([row]), "program")
    assert len(exc_info.value.failure_cases) >= 3


# ---------------------------------------------------------------------------
# The key design claim: noisy-but-valid text must NOT be rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("noisy_status", [
    "ENROLLED", "Enrolled", "enrolled", " ENROLLED ",
    "GRADUATED", "Graduated", "DROPPED", "Dropped", "DROPPED OUT",
])
def test_bronze_schema_accepts_every_day6_noise_variant(noisy_status):
    """Bronze validates SHAPE, not cleanliness -- Day 6's realistic
    enrollment_status noise must pass here. Rejecting it would defeat
    the entire point of generating messy-but-valid source data."""
    row = dict(VALID_ROWS["enrollment"])
    row["enrollment_status"] = noisy_status
    validate_bronze_dataframe(pd.DataFrame([row]), "enrollment")  # should not raise


def test_bronze_schema_rejects_truly_empty_status():
    row = dict(VALID_ROWS["enrollment"])
    row["enrollment_status"] = ""
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_bronze_dataframe(pd.DataFrame([row]), "enrollment")