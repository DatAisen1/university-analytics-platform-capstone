"""
pipelines/silver/cleaning_rules.py

Pure cleaning functions -- kept separate from the DuckDB SQL orchestration
(clean_entities.py) for the same reason every rules module in this project
is separate from its generator/pipeline: independently unit-testable with
plain values in, plain values out.

normalize_enrollment_status is the flagship case: it exists specifically
to resolve the 9 noisy text variants Day 6 deliberately injected
('ENROLLED', 'Enrolled', 'enrolled', ' ENROLLED ', 'GRADUATED', 'Graduated',
'DROPPED', 'Dropped', 'DROPPED OUT') back down to the 3-value controlled
vocabulary (ENROLLED, GRADUATED, DROPPED) that Gold's fact tables and the
dashboard actually consume.
"""

from __future__ import annotations

from typing import Callable, FrozenSet

KNOWN_STATUSES = {"ENROLLED", "GRADUATED", "DROPPED"}


def normalize_enrollment_status(raw: str) -> str:
    """Map any of Day 6's noisy text variants to the controlled
    vocabulary. Raises ValueError for anything genuinely unrecognized --
    callers that need a non-raising version (the DuckDB UDF used in bulk
    cleaning) should use normalize_enrollment_status_safe instead, so a
    single bad row can't abort an entire batch's cleaning pass.
    """
    if raw is None:
        raise ValueError("enrollment_status is null")
    cleaned = raw.strip().upper()
    if cleaned == "DROPPED OUT":
        return "DROPPED"
    if cleaned in KNOWN_STATUSES:
        return cleaned
    raise ValueError(f"Unrecognized enrollment_status value: {raw!r}")


def normalize_enrollment_status_safe(raw: str) -> str:
    """Non-raising variant: unrecognized values are tagged 'UNKNOWN:<raw>'
    rather than raised, so bulk cleaning (Day 10) can process a whole
    batch even if one row is unmappable -- REJECTING that row is Day 11's
    job (quarantine), not this stage's. Cleaning and quarantine are
    deliberately different concerns; see docs/05_Medallion_Architecture.md.
    """
    try:
        return normalize_enrollment_status(raw)
    except ValueError:
        return f"UNKNOWN:{raw}"


def clean_text(raw):
    """Trim leading/trailing whitespace on a text value. Non-string input
    (None, NaN, numbers) passes through unchanged -- trimming is only
    meaningful for strings, and forcing a type conversion here would be
    the cleaning function silently making a decision that belongs to
    schema validation, not text hygiene."""
    return raw.strip() if isinstance(raw, str) else raw


def make_categorical_normalizer(known_values: FrozenSet[str]) -> Callable[[object], object]:
    """Build a case-insensitive normalizer for a small controlled-
    vocabulary column (gender, admission_type, program_level, ...).

    Bug fix: pipelines/silver/clean_entities.py has always imported this
    function (Stage 3, CATEGORICAL STANDARDIZATION) but it was never
    actually defined here, which meant importing clean_entities.py -- and
    therefore running Silver cleaning at all -- raised ImportError. This
    is that missing implementation.

    Returns a function suitable for registering as a DuckDB scalar UDF:
    case-folds any casing variant of a known value onto its canonical
    spelling, and tags anything unrecognized 'UNKNOWN:<raw>' rather than
    dropping or raising -- the same non-raising, tag-don't-reject pattern
    normalize_enrollment_status_safe already established for
    enrollment_status, so callers/quarantine logic can treat every
    categorical column consistently.
    """
    lookup = {v.upper(): v for v in known_values}

    def _normalize(raw):
        cleaned = str(raw).strip().upper()
        return lookup.get(cleaned, f"UNKNOWN:{raw}")

    return _normalize


def normalize_null_like(raw):
    """Turn an empty-or-whitespace-only string into a real null; every
    other value (including non-strings) passes through unchanged. This
    is the same "empty string after trimming means no value" rule
    clean_entities.py's Stage 2 already applies inline via SQL
    (NULLIF(TRIM(...), '')); exposed here as a plain Python function so
    it's independently unit-testable and reusable outside a DuckDB SELECT.
    """
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    return raw


def normalize_semester_number(raw) -> int:
    """Map a semester_number value to the canonical int (1 or 2),
    accepting a plain int, a numeric string, or a '1st/2nd Semester'-
    style label with incidental whitespace/casing. Raises ValueError for
    anything else (None, 0, 3+, unparseable text) -- callers that need a
    non-raising version for bulk cleaning should use
    normalize_semester_number_safe instead, the same raising/safe split
    normalize_enrollment_status already establishes.
    """
    if raw is None:
        raise ValueError("Unrecognized semester value: None")
    text = str(raw).strip().lower()
    if text in ("1", "2"):
        return int(text)
    if text.startswith("1st"):
        return 1
    if text.startswith("2nd"):
        return 2
    raise ValueError(f"Unrecognized semester value: {raw!r}")


def normalize_semester_number_safe(raw):
    """Non-raising variant: an unrecognized value is returned UNCHANGED
    (not tagged, not nulled) so a downstream dtype-coercion failure count
    or business-rule check can catch it -- never a fabricated guess about
    what an unparseable semester value "really" meant.
    """
    try:
        return normalize_semester_number(raw)
    except ValueError:
        return raw