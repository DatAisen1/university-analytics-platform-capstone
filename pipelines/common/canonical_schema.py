"""
pipelines/common/canonical_schema.py

THE authoritative schema for the project's canonical analytical dataset.
Every mart, report, or export that claims to produce "the" university
dataset must conform to CANONICAL_COLUMNS / CANONICAL_DATASET_SCHEMA --
scripts must not invent their own column names for these concepts
(e.g. "college_name" vs "college", "sem" vs "semester"). Import from here.

Grain: one row per (academic_year, semester, college, program, gender,
year_level). Metrics are additive counts at that grain.

Two metrics -- `applicants` and `accepted` -- have NO source system yet.
This project's Bronze/Silver layers ingest enrollment, graduation,
dropout, and shifter events, plus a student master; there is no
admissions/application event stream anywhere in data_generator/ or
pipelines/. Rather than fabricate numbers, this schema declares those
two fields nullable and every builder must emit NULL, not 0 or a guess,
until an admissions source is actually built (tracked as a P1 item).
"""

from __future__ import annotations

from enum import Enum
from typing import Final, List

from pandera.pandas import Column, DataFrameSchema, Check

from pipelines.common.academic_periods import SEMESTER_LABELS, YEAR_LEVEL_LABELS
from pipelines.common.academic_periods import SEMESTER_LABELS, YEAR_LEVEL_LABELS, SUPER_SENIOR_LABEL

class Gender(str, Enum):
    MALE = "Male"
    FEMALE = "Female"


GENDER_VALUES: Final[List[str]] = [g.value for g in Gender]
SEMESTER_VALUES: Final[List[str]] = list(SEMESTER_LABELS)
# Fixed vocabulary regardless of program: Freshman..Senior are absolute
# year_level 1-4 labels; Super Senior is a derived flag (see
# is_super_senior()) computed against each student's own program duration,
# not a 5th absolute year_level bucket. "Graduate" is intentionally absent
# -- it's an outcome, not a year_level (see academic_periods.py).
YEAR_LEVEL_VALUES: Final[List[str]] = list(YEAR_LEVEL_LABELS.values()) + [SUPER_SENIOR_LABEL]

# Dimension (grain) columns -- define the row identity.
CANONICAL_DIMENSION_COLUMNS: Final[List[str]] = [
    "academic_year",
    "semester",
    "college",
    "program",
    "gender",
    "year_level",
]

# Metric columns -- additive counts at that grain.
CANONICAL_METRIC_COLUMNS: Final[List[str]] = [
    "freshmen_count",
    "applicants",
    "accepted",
    "enrolled",
    "graduates",
    "dropouts",
    "shifters",
]

CANONICAL_COLUMNS: Final[List[str]] = CANONICAL_DIMENSION_COLUMNS + CANONICAL_METRIC_COLUMNS

# Metrics with no source system yet -- MUST be emitted as null, never 0/guessed.
UNSOURCED_METRICS: Final[List[str]] = ["applicants", "accepted"]

CANONICAL_DATASET_SCHEMA = DataFrameSchema(
    {
        "academic_year": Column(str, Check.str_matches(r"^\d{4}-\d{4}$"), nullable=False),
        "semester": Column(str, Check.isin(SEMESTER_VALUES), nullable=False),
        "college": Column(str, Check.str_length(min_value=1), nullable=False),
        "program": Column(str, Check.str_length(min_value=1), nullable=False),
        "gender": Column(str, Check.isin(GENDER_VALUES), nullable=False),
        "year_level": Column(str, Check.isin(YEAR_LEVEL_VALUES), nullable=False),
        "freshmen_count": Column(int, Check.ge(0), nullable=False),
        "applicants": Column(float, Check.ge(0), nullable=True),  # float: pandas needs it for NaN support
        "accepted": Column(float, Check.ge(0), nullable=True),
        "enrolled": Column(int, Check.ge(0), nullable=False),
        "graduates": Column(int, Check.ge(0), nullable=False),
        "dropouts": Column(int, Check.ge(0), nullable=False),
        "shifters": Column(int, Check.ge(0), nullable=False),
    },
    strict=True,  # strict=True deliberately, unlike Bronze schemas -- this IS the final contract
)


def validate_canonical_dataset(df):
    """Validate a dataframe against the canonical schema. Raises
    pandera.errors.SchemaErrors (all violations, not just the first) on
    failure. Callers should catch that specific exception."""
    return CANONICAL_DATASET_SCHEMA.validate(df, lazy=True)