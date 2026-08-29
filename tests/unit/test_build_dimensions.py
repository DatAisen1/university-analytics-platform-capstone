"""
tests/unit/test_build_dimensions.py

Tests for pipelines/gold/build_dimensions.py. The SCD2 tests deliberately
include the entry-semester-shift edge case as an explicit regression test
-- this was a REAL bug found while running Day 12 against the actual
dataset (35 students shifted in their very first observed semester,
producing a nonsensical NULL _valid_to_period_key on their "closed" row,
since there's no valid prior semester before their entry to close it at).

Updated for the Task 23/24 Gold Modeling Fix (dim_academic_year +
dim_semester snowflake pair collapsed into one denormalized
dim_academic_period table), P0.4's correction of the dataset horizon
from 4 cohorts / 8 periods to 3 cohorts / 6 periods (2021-2022 through
2023-2024), and the later P0 Dataset Extension task, which grew the
canonical horizon again to 5 cohorts / 10 periods (2021-2022 through
2025-2026). See build_dimensions.py's module docstring and
pipelines.common.academic_periods (the single canonical source for the
observed window) for the full rationale.
"""

import pandas as pd
import pytest

from pipelines.gold.build_dimensions import (
    academic_period_key_lookup,
    build_dim_academic_period,
    build_dim_calendar,
    build_dim_college,
    build_dim_program,
    build_dim_gender,
    build_dim_student,
    period_ordinal,
)


@pytest.fixture
def dim_academic_period():
    return build_dim_academic_period()


@pytest.fixture
def dim_gender():
    return build_dim_gender()


@pytest.fixture
def dim_college():
    college_df = pd.DataFrame([
        {"college_id": "COA", "college_name": "College of Architecture"},
        {"college_id": "CICT", "college_name": "College of ICT"},
    ])
    return build_dim_college(college_df)


@pytest.fixture
def dim_program(dim_college):
    program_df = pd.DataFrame([
        {"program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
         "program_level": "Bachelor", "nominal_duration_years": 5.0},
        {"program_id": "CICT-BSIT-WEB", "program_name": "BSIT Web", "college_id": "CICT",
         "program_level": "Bachelor", "nominal_duration_years": 4.0},
    ])
    return build_dim_program(program_df, dim_college)


# ---------------------------------------------------------------------------
# period_ordinal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "year,sem,expected",
    [(2021, 1, 0), (2021, 2, 1), (2022, 1, 2), (2023, 2, 5), (2025, 2, 9)],
)
def test_period_ordinal(year, sem, expected):
    assert period_ordinal(year, sem) == expected


# ---------------------------------------------------------------------------
# dim_academic_period / dim_calendar
# ---------------------------------------------------------------------------

def test_dim_academic_period_has_ten_rows_across_five_years(dim_academic_period):
    """Canonical horizon (P0 Dataset Extension): 5 academic years x 2
    semesters = 10 periods -- extended from the earlier P0.4 correction's
    3 years / 6 periods, not the original, incorrect 4-cohort / 8-period
    model either."""
    assert len(dim_academic_period) == 10
    assert list(dim_academic_period["academic_year"].unique()) == [2021, 2022, 2023, 2024, 2025]
    assert set(dim_academic_period["period_label"]) == {
        "2021-2022 \u00b7 1st Semester", "2021-2022 \u00b7 2nd Semester",
        "2022-2023 \u00b7 1st Semester", "2022-2023 \u00b7 2nd Semester",
        "2023-2024 \u00b7 1st Semester", "2023-2024 \u00b7 2nd Semester",
        "2024-2025 \u00b7 1st Semester", "2024-2025 \u00b7 2nd Semester",
        "2025-2026 \u00b7 1st Semester", "2025-2026 \u00b7 2nd Semester",
    }


def test_dim_academic_period_key_equals_ordinal_plus_one(dim_academic_period):
    assert list(dim_academic_period["academic_period_key"]) == list(dim_academic_period["period_ordinal"] + 1)


def test_dim_calendar_spans_full_range(dim_academic_period):
    calendar = build_dim_calendar(dim_academic_period)
    assert calendar["full_date"].min().isoformat() == "2021-01-01"
    assert calendar["full_date"].max().isoformat() == "2025-12-31"
    assert len(calendar) == 1826  # 5 years incl. one leap year (2024)


def test_dim_calendar_every_row_has_valid_period_key(dim_academic_period):
    calendar = build_dim_calendar(dim_academic_period)
    valid_keys = set(dim_academic_period["academic_period_key"])
    assert calendar["academic_period_key"].isin(valid_keys).all()


# ---------------------------------------------------------------------------
# dim_college / dim_program
# ---------------------------------------------------------------------------

def test_dim_college_assigns_surrogate_keys():
    college_df = pd.DataFrame([
        {"college_id": "COA", "college_name": "College of Architecture"},
        {"college_id": "CICT", "college_name": "College of ICT"},
    ])
    dim_college = build_dim_college(college_df)
    assert list(dim_college["college_key"]) == [1, 2]
    assert set(dim_college["college_id"]) == {"COA", "CICT"}


def test_dim_program_resolves_college_key():
    college_df = pd.DataFrame([{"college_id": "COA", "college_name": "College of Architecture"}])
    dim_college = build_dim_college(college_df)
    program_df = pd.DataFrame([{
        "program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
        "program_level": "Bachelor", "nominal_duration_years": 5.0,
    }])
    dim_program = build_dim_program(program_df, dim_college)
    assert dim_program.iloc[0]["college_key"] == dim_college.iloc[0]["college_key"]


# ---------------------------------------------------------------------------
# dim_student -- SCD2
# ---------------------------------------------------------------------------

def _program_key(dim_program, program_id):
    return dim_program.loc[dim_program["program_id"] == program_id, "program_key"].iloc[0]


def test_student_with_no_shift_gets_exactly_one_open_row(dim_academic_period, dim_gender, dim_college, dim_program):
    student_df = pd.DataFrame([{
        "student_id": "S1", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman",
        "entry_college_id": "COA", "entry_program_id": "COA-BSARCH",
    }])
    shifter_df = pd.DataFrame(columns=["student_id", "academic_year", "semester_number",
                                        "from_program_id", "to_program_id"])

    dim_student = build_dim_student(student_df, shifter_df, dim_academic_period, dim_gender, dim_college, dim_program)
    assert len(dim_student) == 1
    assert dim_student.iloc[0]["_is_current"] == True  # noqa: E712
    assert pd.isna(dim_student.iloc[0]["_valid_to_period_key"])
    assert dim_student.iloc[0]["program_key"] == _program_key(dim_program, "COA-BSARCH")


def test_student_with_mid_history_shift_gets_two_rows_properly_closed(dim_academic_period, dim_gender, dim_college, dim_program):
    """The normal case: a student enters 2021-1, shifts in 2021-2 -- the
    first row must close at 2021-1 (the semester BEFORE the shift), and
    the second row must be open (_is_current=True)."""
    student_df = pd.DataFrame([{
        "student_id": "S1", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman",
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSIT-WEB",
    }])
    shifter_df = pd.DataFrame([{
        "student_id": "S1", "academic_year": 2021, "semester_number": 2,
        "from_program_id": "CICT-BSIT-WEB", "to_program_id": "COA-BSARCH",
    }])

    dim_student = build_dim_student(student_df, shifter_df, dim_academic_period, dim_gender, dim_college, dim_program)
    assert len(dim_student) == 2

    period_key = academic_period_key_lookup(dim_academic_period)
    old_row = dim_student[~dim_student["_is_current"]].iloc[0]
    new_row = dim_student[dim_student["_is_current"]].iloc[0]

    assert old_row["program_key"] == _program_key(dim_program, "CICT-BSIT-WEB")
    assert old_row["_valid_from_period_key"] == period_key[(2021, 1)]
    assert old_row["_valid_to_period_key"] == period_key[(2021, 1)]  # closes at the semester BEFORE the shift

    assert new_row["program_key"] == _program_key(dim_program, "COA-BSARCH")
    assert new_row["_valid_from_period_key"] == period_key[(2021, 2)]
    assert pd.isna(new_row["_valid_to_period_key"])


def test_student_who_shifts_in_their_entry_semester_gets_exactly_one_row(dim_academic_period, dim_gender, dim_college, dim_program):
    """REGRESSION TEST for the real Day 12 bug: a shift occurring in the
    student's very first observed semester has no valid prior semester to
    close a row at (Day 5's simulate_student applies the shift check
    before emitting that semester's own enrollment record, so the very
    first record already reflects the post-shift program). This must
    produce exactly ONE row -- open, with the POST-shift program -- not a
    doomed 'closed' row with a nonsensical null _valid_to_period_key."""
    student_df = pd.DataFrame([{
        "student_id": "S1", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman",
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSIT-WEB",
    }])
    shifter_df = pd.DataFrame([{
        "student_id": "S1", "academic_year": 2021, "semester_number": 1,  # SAME as entry semester
        "from_program_id": "CICT-BSIT-WEB", "to_program_id": "COA-BSARCH",
    }])

    dim_student = build_dim_student(student_df, shifter_df, dim_academic_period, dim_gender, dim_college, dim_program)

    assert len(dim_student) == 1
    row = dim_student.iloc[0]
    assert row["_is_current"] == True  # noqa: E712
    assert pd.isna(row["_valid_to_period_key"])
    assert row["program_key"] == _program_key(dim_program, "COA-BSARCH")  # the POST-shift program, carried from the start


def test_dim_student_exactly_one_current_row_per_student_at_scale(dim_academic_period, dim_gender, dim_college, dim_program):
    """A broader property test: across many students with a mix of no
    shifts, mid-history shifts, and entry-semester shifts, every student
    must end up with EXACTLY one current row -- the Day 12 validation
    checklist item, made explicit as a test."""
    student_rows = []
    shifter_rows = []
    for i in range(20):
        sid = f"S{i}"
        student_rows.append({
            "student_id": sid, "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
            "home_province": "Nueva Ecija", "admission_type": "Freshman",
            "entry_college_id": "CICT", "entry_program_id": "CICT-BSIT-WEB",
        })
        if i % 3 == 0:  # every third student shifts mid-history
            shifter_rows.append({
                "student_id": sid, "academic_year": 2021, "semester_number": 2,
                "from_program_id": "CICT-BSIT-WEB", "to_program_id": "COA-BSARCH",
            })
        elif i % 3 == 1:  # every third-plus-one shifts in their ENTRY semester
            shifter_rows.append({
                "student_id": sid, "academic_year": 2021, "semester_number": 1,
                "from_program_id": "CICT-BSIT-WEB", "to_program_id": "COA-BSARCH",
            })

    student_df = pd.DataFrame(student_rows)
    shifter_df = pd.DataFrame(shifter_rows)

    dim_student = build_dim_student(student_df, shifter_df, dim_academic_period, dim_gender, dim_college, dim_program)

    current_counts = dim_student[dim_student["_is_current"]].groupby("student_id").size()
    assert (current_counts == 1).all()
    assert len(current_counts) == 20  # every student has exactly one current row

    non_current = dim_student[~dim_student["_is_current"]]
    assert non_current["_valid_to_period_key"].isna().sum() == 0  # no closed row ever left null