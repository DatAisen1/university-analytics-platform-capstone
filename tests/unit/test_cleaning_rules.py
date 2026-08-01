"""
tests/unit/test_cleaning_rules.py

Unit tests for pipelines/silver/cleaning_rules.py, with edge-case inputs
per Day 10's testing checklist -- not just the 9 known Day 6 noise
variants, but null, empty, and genuinely garbage input too.
"""

import pytest

from pipelines.silver.cleaning_rules import (
    clean_text,
    make_categorical_normalizer,
    normalize_enrollment_status,
    normalize_enrollment_status_safe,
    normalize_null_like,
    normalize_semester_number,
    normalize_semester_number_safe,
)


# ---------------------------------------------------------------------------
# normalize_enrollment_status -- all 9 real Day 6 noise variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ENROLLED", "ENROLLED"),
    ("Enrolled", "ENROLLED"),
    ("enrolled", "ENROLLED"),
    (" ENROLLED ", "ENROLLED"),
    ("GRADUATED", "GRADUATED"),
    ("Graduated", "GRADUATED"),
    ("DROPPED", "DROPPED"),
    ("Dropped", "DROPPED"),
    ("DROPPED OUT", "DROPPED"),
])
def test_normalize_enrollment_status_handles_every_day6_variant(raw, expected):
    assert normalize_enrollment_status(raw) == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_normalize_enrollment_status_none_raises_value_error():
    with pytest.raises(ValueError, match="null"):
        normalize_enrollment_status(None)


def test_normalize_enrollment_status_empty_string_raises_value_error():
    with pytest.raises(ValueError, match="Unrecognized"):
        normalize_enrollment_status("")


def test_normalize_enrollment_status_whitespace_only_raises_value_error():
    with pytest.raises(ValueError, match="Unrecognized"):
        normalize_enrollment_status("   ")


def test_normalize_enrollment_status_garbage_raises_value_error():
    with pytest.raises(ValueError, match="Unrecognized"):
        normalize_enrollment_status("ON_LEAVE")  # a real vocabulary value the generator never produces, but Silver must still handle gracefully


def test_normalize_enrollment_status_mixed_case_with_extra_whitespace():
    assert normalize_enrollment_status("  gRaDuAtEd  ") == "GRADUATED"


def test_normalize_enrollment_status_safe_returns_tagged_unknown_instead_of_raising():
    result = normalize_enrollment_status_safe("TOTALLY_BOGUS")
    assert result == "UNKNOWN:TOTALLY_BOGUS"


def test_normalize_enrollment_status_safe_passes_through_valid_values():
    assert normalize_enrollment_status_safe("Enrolled") == "ENROLLED"


def test_normalize_enrollment_status_safe_never_raises_on_none():
    result = normalize_enrollment_status_safe(None)
    assert result.startswith("UNKNOWN:")


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

def test_clean_text_trims_whitespace():
    assert clean_text("  Nueva Ecija  ") == "Nueva Ecija"


def test_clean_text_leaves_already_clean_text_unchanged():
    assert clean_text("Nueva Ecija") == "Nueva Ecija"


def test_clean_text_passes_through_non_string_unchanged():
    assert clean_text(42) == 42
    assert clean_text(None) is None
    assert clean_text(3.14) == 3.14


def test_clean_text_empty_string_stays_empty():
    assert clean_text("") == ""


def test_clean_text_only_whitespace_becomes_empty():
    assert clean_text("   ") == ""


# ---------------------------------------------------------------------------
# normalize_null_like
# ---------------------------------------------------------------------------

def test_normalize_null_like_empty_string_becomes_none():
    assert normalize_null_like("") is None


def test_normalize_null_like_whitespace_only_becomes_none():
    assert normalize_null_like("   ") is None


def test_normalize_null_like_real_text_passes_through():
    assert normalize_null_like("Nueva Ecija") == "Nueva Ecija"


def test_normalize_null_like_non_string_passes_through():
    assert normalize_null_like(42) == 42
    assert normalize_null_like(None) is None


# ---------------------------------------------------------------------------
# normalize_semester_number / normalize_semester_number_safe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (1, 1), (2, 2), ("1", 1), ("2", 2),
    (" 1 ", 1), ("1st Semester", 1), ("2nd Semester", 2),
])
def test_normalize_semester_number_handles_every_expected_variant(raw, expected):
    assert normalize_semester_number(raw) == expected


@pytest.mark.parametrize("raw", [0, 3, "3rd Semester", None, "garbage", True])
def test_normalize_semester_number_rejects_invalid_values(raw):
    with pytest.raises(ValueError, match="Unrecognized semester value"):
        normalize_semester_number(raw)


def test_normalize_semester_number_safe_passes_through_valid_values():
    assert normalize_semester_number_safe(1) == 1
    assert normalize_semester_number_safe("2nd Semester") == 2


def test_normalize_semester_number_safe_returns_raw_unchanged_on_bad_input():
    """Unlike the categorical normalizer, an unrecognized semester is NOT
    tagged -- it's returned as-is so a downstream dtype-coercion count or
    business-rule check can catch it, never a fabricated guess."""
    assert normalize_semester_number_safe("3rd Semester") == "3rd Semester"
    assert normalize_semester_number_safe(None) is None


# ---------------------------------------------------------------------------
# make_categorical_normalizer
# ---------------------------------------------------------------------------

def test_make_categorical_normalizer_maps_case_and_whitespace_variants():
    normalize_gender = make_categorical_normalizer({"Male", "Female"})
    assert normalize_gender("male") == "Male"
    assert normalize_gender(" FEMALE ") == "Female"
    assert normalize_gender("Female") == "Female"


def test_make_categorical_normalizer_tags_unknown_values():
    normalize_gender = make_categorical_normalizer({"Male", "Female"})
    assert normalize_gender("Nonbinary") == "UNKNOWN:Nonbinary"


def test_make_categorical_normalizer_tags_null_and_empty():
    normalize_gender = make_categorical_normalizer({"Male", "Female"})
    assert normalize_gender(None) == "UNKNOWN:None"
    assert normalize_gender("   ") == "UNKNOWN:   "