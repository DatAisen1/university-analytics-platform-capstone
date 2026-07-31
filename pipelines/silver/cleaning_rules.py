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
