"""
tests/unit/test_academic_periods.py

Task 48: dedicated coverage for pipelines/common/academic_periods.py --
the single source of truth for academic-year label formatting, semester
labels/ordering, year-level labels (including the Super Senior rule),
and cohort -> year_level derivation. These primitives are consumed by
every layer (data_generator, silver, gold, dbt macros), so a regression
here silently corrupts labels everywhere downstream; prior to this file
none of them had direct unit coverage.
"""

from __future__ import annotations

import pandas as pd
import pytest

from pipelines.common.academic_periods import (
    OBSERVED_START_YEAR,
    SEMESTER_LABELS,
    YEAR_LEVEL_LABELS,
    SUPER_SENIOR_LABEL,
    academic_period_index,
    academic_year_categorical_dtype,
    academic_year_end_year,
    academic_year_index,
    academic_year_label,
    academic_year_start_year,
    is_super_senior,
    period_label_from_index,
    semester_categorical_dtype,
    semester_label_from_number,
    sort_by_academic_period,
    year_level_for_cohort,
    year_level_label,
)


# --------------------------------------------------------------------------
# academic_year_label / academic_year_start_year / academic_year_end_year
# --------------------------------------------------------------------------

def test_academic_year_label_formats_int_start_year():
    assert academic_year_label(2021) == "2021-2022"


def test_academic_year_label_formats_bare_string_start_year():
    assert academic_year_label("2022") == "2022-2023"


def test_academic_year_label_passes_through_already_formatted_string():
    assert academic_year_label("2023-2024") == "2023-2024"


def test_academic_year_start_year_defaults_to_observed_start_when_none():
    assert academic_year_start_year(None) == OBSERVED_START_YEAR


def test_academic_year_start_year_parses_dashed_label():
    assert academic_year_start_year("2022-2023") == 2022


def test_academic_year_start_year_parses_bare_string():
    assert academic_year_start_year("2022") == 2022


def test_academic_year_start_year_passes_through_int():
    assert academic_year_start_year(2024) == 2024


def test_academic_year_end_year_is_start_plus_one():
    assert academic_year_end_year("2022-2023") == 2023
    assert academic_year_end_year(2021) == 2022


def test_academic_year_index_is_zero_for_observed_start_year():
    assert academic_year_index(OBSERVED_START_YEAR) == 0


def test_academic_year_index_increments_per_year():
    assert academic_year_index(OBSERVED_START_YEAR + 1) == 1
    assert academic_year_index(f"{OBSERVED_START_YEAR + 3}-{OBSERVED_START_YEAR + 4}") == 3


# --------------------------------------------------------------------------
# academic_period_index / semester_label_from_number / period_label_from_index
# --------------------------------------------------------------------------

def test_academic_period_index_first_semester_of_observed_start_year_is_zero():
    assert academic_period_index(OBSERVED_START_YEAR, "1st Semester") == 0


def test_academic_period_index_second_semester_adds_one():
    assert academic_period_index(OBSERVED_START_YEAR, "2nd Semester") == 1
    assert academic_period_index(OBSERVED_START_YEAR, 2) == 1


def test_academic_period_index_defaults_to_first_semester_when_unspecified():
    assert academic_period_index(OBSERVED_START_YEAR, None) == 0


def test_academic_period_index_advances_by_two_per_academic_year():
    assert academic_period_index(OBSERVED_START_YEAR + 2, "1st Semester") == 4


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, "1st Semester"),
        ("1st Semester", "1st Semester"),
        (2, "2nd Semester"),
        ("2nd Semester", "2nd Semester"),
    ],
)
def test_semester_label_from_number_matches_expected_variants(raw, expected):
    assert semester_label_from_number(raw) == expected


def test_semester_label_from_number_defaults_to_first_semester_for_unknown_input():
    assert semester_label_from_number(None) == "1st Semester"
    assert semester_label_from_number("garbage") == "1st Semester"


def test_period_label_from_index_round_trips_with_academic_period_index():
    for index in range(0, 8):
        year_label, semester_label = period_label_from_index(index)
        assert academic_period_index(year_label, semester_label) == index


def test_period_label_from_index_zero_is_first_semester_of_observed_start_year():
    year_label, semester_label = period_label_from_index(0)
    assert year_label == academic_year_label(OBSERVED_START_YEAR)
    assert semester_label == SEMESTER_LABELS[0]


# --------------------------------------------------------------------------
# is_super_senior / year_level_label
# --------------------------------------------------------------------------

def test_is_super_senior_raises_on_non_positive_duration():
    with pytest.raises(ValueError):
        is_super_senior(5, 0)
    with pytest.raises(ValueError):
        is_super_senior(5, -1)


def test_is_super_senior_false_when_year_level_within_program_duration():
    # 5-year program, year_level 5 -> on time, not a Super Senior.
    assert is_super_senior(5, 5.0) is False


def test_is_super_senior_true_when_year_level_exceeds_program_duration():
    # 4-year program, year_level 5 -> exceeded standard duration.
    assert is_super_senior(5, 4.0) is True


def test_is_super_senior_uses_ceiling_for_fractional_durations():
    # 4.5-year program rounds up to a 5-year cutoff via math.ceil.
    assert is_super_senior(5, 4.5) is False
    assert is_super_senior(6, 4.5) is True


@pytest.mark.parametrize("level,label", list(YEAR_LEVEL_LABELS.items()))
def test_year_level_label_returns_documented_label_for_levels_one_through_four(level, label):
    assert year_level_label(level, nominal_duration_years=4.0) == label


def test_year_level_label_defaults_none_to_freshman():
    assert year_level_label(None, nominal_duration_years=4.0) == "Freshman"


def test_year_level_label_floors_below_one_to_freshman():
    assert year_level_label(0, nominal_duration_years=4.0) == "Freshman"
    assert year_level_label(-3, nominal_duration_years=4.0) == "Freshman"


def test_year_level_label_on_time_extra_year_in_long_program_is_still_senior():
    # A 5-year Engineering student at year_level 5 is on time -> "Senior",
    # never "Graduate" (graduation is an outcome, not a year_level).
    assert year_level_label(5, nominal_duration_years=5.0) == "Senior"


def test_year_level_label_past_duration_is_super_senior():
    # A 4-year IT student at year_level 5 has exceeded standard duration.
    assert year_level_label(5, nominal_duration_years=4.0) == SUPER_SENIOR_LABEL


def test_year_level_label_accepts_string_year_level():
    assert year_level_label("2", nominal_duration_years=4.0) == "Sophomore"


# --------------------------------------------------------------------------
# year_level_for_cohort
# --------------------------------------------------------------------------

def test_year_level_for_cohort_entering_in_or_after_observed_start_is_freshman():
    assert year_level_for_cohort(OBSERVED_START_YEAR) == 1
    assert year_level_for_cohort(OBSERVED_START_YEAR + 1) == 1


def test_year_level_for_cohort_advances_one_level_per_elapsed_year():
    assert year_level_for_cohort(OBSERVED_START_YEAR - 1) == 2
    assert year_level_for_cohort(OBSERVED_START_YEAR - 2) == 3


def test_year_level_for_cohort_is_capped_at_six():
    assert year_level_for_cohort(OBSERVED_START_YEAR - 10) == 6


def test_year_level_for_cohort_accepts_dashed_label_input():
    assert year_level_for_cohort(f"{OBSERVED_START_YEAR - 1}-{OBSERVED_START_YEAR}") == 2


# --------------------------------------------------------------------------
# academic_year_categorical_dtype / semester_categorical_dtype
# --------------------------------------------------------------------------

def test_academic_year_categorical_dtype_orders_years_chronologically():
    dtype = academic_year_categorical_dtype([2023, 2021, 2022])
    assert list(dtype.categories) == ["2021-2022", "2022-2023", "2023-2024"]
    assert dtype.ordered is True


def test_academic_year_categorical_dtype_defaults_to_three_year_observed_window():
    """P0.4: the canonical dataset horizon is 3 academic years (2021-2022
    through 2023-2024), not the pre-migration 4-year window -- see
    OBSERVED_START_YEAR's docstring and academic_year_categorical_dtype's
    `years or range(OBSERVED_START_YEAR, OBSERVED_START_YEAR + 3)` default."""
    dtype = academic_year_categorical_dtype()
    assert list(dtype.categories) == [
        academic_year_label(y) for y in range(OBSERVED_START_YEAR, OBSERVED_START_YEAR + 3)
    ]


def test_semester_categorical_dtype_orders_first_before_second():
    dtype = semester_categorical_dtype()
    assert list(dtype.categories) == ["1st Semester", "2nd Semester"]
    assert dtype.ordered is True


# --------------------------------------------------------------------------
# sort_by_academic_period
# --------------------------------------------------------------------------

def _unsorted_period_frame() -> pd.DataFrame:
    # Deliberately out of both insertion and alphabetical order: alphabetical
    # sort on academic_year alone would put "2021-2022" before "2022-2023"
    # correctly, but WOULD mis-order a wider year range (e.g. "2029-2030"
    # would sort before "2121-2122" alphabetically). Building the frame with
    # scrambled row order exercises the real bug this helper exists to avoid.
    return pd.DataFrame(
        {
            "academic_year": ["2022-2023", "2021-2022", "2022-2023", "2021-2022"],
            "semester": ["2nd Semester", "2nd Semester", "1st Semester", "1st Semester"],
            "value": [4, 2, 3, 1],
        }
    )


def test_sort_by_academic_period_orders_chronologically_not_alphabetically():
    result = sort_by_academic_period(_unsorted_period_frame())
    assert result["value"].tolist() == [1, 2, 3, 4]


def test_sort_by_academic_period_does_not_mutate_input():
    original = _unsorted_period_frame()
    original_copy = original.copy()
    sort_by_academic_period(original)
    pd.testing.assert_frame_equal(original, original_copy)


def test_sort_by_academic_period_drops_helper_sort_columns():
    result = sort_by_academic_period(_unsorted_period_frame())
    assert "_ay_sort" not in result.columns
    assert "_sem_sort" not in result.columns


def test_sort_by_academic_period_returns_plain_string_dtype_columns():
    # The categorical dtype used internally for sorting must not leak into
    # the returned frame -- downstream code expects plain strings back.
    result = sort_by_academic_period(_unsorted_period_frame())
    assert not isinstance(result["academic_year"].dtype, pd.CategoricalDtype)
    assert not isinstance(result["semester"].dtype, pd.CategoricalDtype)
    assert result["academic_year"].tolist() == ["2021-2022", "2021-2022", "2022-2023", "2022-2023"]


def test_sort_by_academic_period_respects_additional_trailing_sort_columns():
    df = pd.DataFrame(
        {
            "academic_year": ["2021-2022", "2021-2022"],
            "semester": ["1st Semester", "1st Semester"],
            "college": ["COE", "CAS"],
            "value": [2, 1],
        }
    )
    result = sort_by_academic_period(df, other_sort_cols=["college"])
    assert result["college"].tolist() == ["CAS", "COE"]