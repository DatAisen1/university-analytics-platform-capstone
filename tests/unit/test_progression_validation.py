"""Unit tests for pipelines/silver/progression_validation.py (P0 #13)."""

from __future__ import annotations

import pandas as pd

from pipelines.silver.progression_validation import (
    check_year_level_progression,
    find_impossible_year_level_transitions,
)


def _row(student_id: str, academic_year: int, semester_number: int, year_level: int) -> dict:
    return {
        "student_id": student_id, "academic_year": academic_year,
        "semester_number": semester_number, "year_level": year_level,
    }


def test_normal_progression_is_not_flagged():
    df = pd.DataFrame([_row("S1", 2021, 1, 1), _row("S1", 2021, 2, 2)])
    assert find_impossible_year_level_transitions(df).empty


def test_stall_is_not_flagged():
    df = pd.DataFrame([_row("S1", 2021, 1, 1), _row("S1", 2021, 2, 1)])
    assert find_impossible_year_level_transitions(df).empty


def test_freshman_to_senior_jump_is_flagged():
    df = pd.DataFrame([_row("S1", 2021, 1, 1), _row("S1", 2021, 2, 4)])
    violations = find_impossible_year_level_transitions(df)
    assert len(violations) == 1
    assert violations.iloc[0]["_transition_violation"] == "1 -> 4"


def test_decrease_is_flagged():
    df = pd.DataFrame([_row("S1", 2021, 1, 4), _row("S1", 2021, 2, 3)])
    violations = find_impossible_year_level_transitions(df)
    assert len(violations) == 1
    assert violations.iloc[0]["_transition_violation"] == "4 -> 3"


def test_gap_is_not_evaluated():
    """A non-consecutive semester pair (a gap) is out of scope -- must
    not be flagged even though 1 -> 4 would be impossible if adjacent."""
    df = pd.DataFrame([_row("S1", 2021, 1, 1), _row("S1", 2023, 1, 4)])
    assert find_impossible_year_level_transitions(df).empty


def test_check_year_level_progression_quarantines_only_bad_rows():
    df = pd.DataFrame([
        _row("S1", 2021, 1, 1),
        _row("S1", 2021, 2, 4),   # impossible jump
        _row("S2", 2021, 1, 1),
        _row("S2", 2021, 2, 2),   # fine
    ])
    valid, quarantined = check_year_level_progression(df)
    assert len(quarantined) == 1
    assert len(valid) == 3
    assert quarantined.iloc[0]["student_id"] == "S1"