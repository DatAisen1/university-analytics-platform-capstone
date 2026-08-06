from __future__ import annotations
import pandas as pd

from typing import Tuple
import math

OBSERVED_START_YEAR = 2021
SEMESTER_LABELS = ("1st Semester", "2nd Semester")
YEAR_LEVEL_LABELS = {
    1: "Freshman",
    2: "Sophomore",
    3: "Junior",
    4: "Senior",
}
SUPER_SENIOR_LABEL = "Super Senior"


def academic_year_label(start_year: int | str) -> str:
    if isinstance(start_year, str):
        if "-" in start_year:
            return start_year
        start_year = int(start_year)
    return f"{start_year}-{start_year + 1}"


def academic_year_start_year(value: int | str | None) -> int:
    if value is None:
        return OBSERVED_START_YEAR
    if isinstance(value, str):
        if "-" in value:
            return int(value.split("-")[0])
        return int(value)
    return int(value)


def academic_year_end_year(value: int | str | None) -> int:
    return academic_year_start_year(value) + 1


def academic_year_index(value: int | str | None) -> int:
    return academic_year_start_year(value) - OBSERVED_START_YEAR


def academic_period_index(academic_year: int | str, semester_name: str | int | None = None) -> int:
    base = academic_year_index(academic_year) * 2
    if semester_name in {"2nd Semester", 2}:
        return base + 1
    return base


def semester_label_from_number(semester_number: int | str | None) -> str:
    if semester_number in {"2nd Semester", 2}:
        return "2nd Semester"
    return "1st Semester"


def period_label_from_index(index: int) -> Tuple[str, str]:
    academic_year = academic_year_label(OBSERVED_START_YEAR + index // 2)
    semester_name = SEMESTER_LABELS[index % 2]
    return academic_year, semester_name


def is_super_senior(year_level: int, nominal_duration_years: float) -> bool:
    """THE canonical Super Senior rule for this project (docs/15_Student_
    Lifecycle_Rules.md): a student whose year_level has gone PAST their
    program's own standard duration while still active.

    `nominal_duration_years` MUST come from the student's actual program
    (configs/programs.yaml / dim_program) -- never a project-wide
    constant. A 5-year Engineering student at year_level 5 is on time; a
    4-year IT student at year_level 5 is a Super Senior. Using a single
    absolute year_level cutoff for both was the original bug.

    Note this function answers only the "exceeded standard duration"
    half of the rule -- the "remains enrolled" half must be checked by
    the caller against enrollment_status (a GRADUATED or DROPPED student
    is not a Super Senior no matter their year_level).
    """
    if nominal_duration_years <= 0:
        raise ValueError(f"nominal_duration_years must be positive, got {nominal_duration_years}")
    return year_level > math.ceil(nominal_duration_years)


def year_level_label(year_level: int | str | None, nominal_duration_years: float) -> str:
    """Program-aware year_level label. `nominal_duration_years` is now a
    REQUIRED argument -- there is no such thing as a program-independent
    year_level label past year 4 in this project. "Graduate" is
    deliberately not a possible return value: graduation is an outcome
    (fact_graduation / enrollment_status == 'GRADUATED'), never a
    year_level -- conflating the two mislabeled active, stalled students
    in long-duration programs as graduates.
    """
    if year_level is None:
        level = 1
    else:
        level = int(year_level)
    level = max(1, level)

    if level <= 4:
        return YEAR_LEVEL_LABELS[level]
    if is_super_senior(level, nominal_duration_years):
        return SUPER_SENIOR_LABEL
    return YEAR_LEVEL_LABELS[4]  # an on-time extra year in a 5+-year program is still "Senior"


def year_level_for_cohort(start_year: int | str | None, observed_start_year: int = OBSERVED_START_YEAR) -> int:
    start_year = academic_year_start_year(start_year)
    if start_year >= observed_start_year:
        return 1
    elapsed_years = observed_start_year - start_year
    return min(6, max(1, 1 + elapsed_years))

def academic_year_categorical_dtype(years: list[int] | None = None) -> pd.CategoricalDtype:
    """Ordered CategoricalDtype for academic_year LABELS (e.g. '2021-2022'),
    in true chronological order. Use this instead of default string sort
    anywhere academic_year is grouped/plotted/displayed:

        df["academic_year"] = df["academic_year"].astype(academic_year_categorical_dtype())
        df = df.sort_values("academic_year")   # now chronological, not alphabetical
    """
    years = years or range(OBSERVED_START_YEAR, OBSERVED_START_YEAR + 3)
    ordered_labels = [academic_year_label(y) for y in sorted(set(years))]
    return pd.CategoricalDtype(categories=ordered_labels, ordered=True)


def semester_categorical_dtype() -> pd.CategoricalDtype:
    """Ordered CategoricalDtype for semester labels: '1st Semester' before
    '2nd Semester'. Alphabetical sort would put them in the same order by
    coincidence for these two specific strings, but relying on that is
    fragile and undocumented -- use this explicitly instead."""
    return pd.CategoricalDtype(categories=list(SEMESTER_LABELS), ordered=True)


def sort_by_academic_period(
    df: pd.DataFrame,
    academic_year_col: str = "academic_year",
    semester_col: str = "semester",
    other_sort_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Sort `df` in true chronological (academic_year, semester) order,
    optionally with additional trailing sort columns (e.g. college, program).
    Returns a new dataframe; does not mutate the input, and does not leave
    the categorical dtype conversion behind (columns are returned as plain
    str, so downstream code that expects strings doesn't break)."""
    working = df.copy()
    years_present = {academic_year_start_year(v) for v in working[academic_year_col].unique()}
    working["_ay_sort"] = working[academic_year_col].astype(academic_year_categorical_dtype(years_present))
    working["_sem_sort"] = working[semester_col].astype(semester_categorical_dtype())

    sort_cols = ["_ay_sort", "_sem_sort"] + (other_sort_cols or [])
    working = working.sort_values(sort_cols).drop(columns=["_ay_sort", "_sem_sort"]).reset_index(drop=True)
    return working