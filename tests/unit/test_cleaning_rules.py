"""
tests/unit/test_cleaning_rules.py

Unit tests for pipelines/silver/cleaning_rules.py, with edge-case inputs
per Day 10's testing checklist -- not just the 9 known Day 6 noise
variants, but null, empty, and genuinely garbage input too.
"""

import pytest

from pipelines.silver.cleaning_rules import (
    clean_text,
    normalize_enrollment_status,
    normalize_enrollment_status_safe,
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
