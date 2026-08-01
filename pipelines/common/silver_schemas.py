"""
pipelines/common/silver_schemas.py

Pandera DataFrameSchemas for the SILVER layer -- validating the actual
post-cleaning shape that pipelines/silver/clean_entities.py produces
(nullable Int64/string/boolean dtypes, coerced academic_year/
semester_number, case-folded categoricals), not the Bronze layer's raw
shape (see pipelines/common/schemas.py for that).

This is a genuinely different job from Bronze's schema module, not a
copy of it:
  - Bronze validates what was RECEIVED (closer to source shape, values
    the source system's own types/casing).
  - Silver validates what CLEANING PRODUCED -- the canonical dtypes and
    controlled vocabularies clean_entities.py is supposed to guarantee.
    A Silver schema failure means the CLEANING logic has a bug, not that
    a messy source sent bad data (that's an expected, handled case at
    this layer already).

Column-level, SINGLE-ENTITY checks only (academic_year, semester,
college_id/program_id presence+shape, gender, year_level, required
metrics -- Task 21's explicit list). Cross-entity/referential rules
("program belongs to college", "accepted <= applicants", ...) are
deliberately NOT here -- see pipelines/silver/business_rules.py, which
is where multi-table joins and business-relationship checks (Task 22)
live, mirroring the same schema/business-rule separation this project
already uses between pipelines/common/schemas.py (Bronze shape) and
pipelines/silver/validate_and_dedupe.py (Bronze->Silver business rules).

Run standalone via: python -m pipelines.common.silver_schemas
"""

from __future__ import annotations

from typing import Dict

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema

# Nullable pandas extension dtypes -- must match clean_entities.py's
# TARGET_DTYPES exactly, since these schemas validate CLEANING's output.
_STR = pd.StringDtype()
_INT = pd.Int64Dtype()
_BOOL = pd.BooleanDtype()

# Data-driven, not magic numbers: mirrors this project's actual 4-cohort
# generation window (data_generator/config/volumes.yaml) and the 2
# semesters/year modeled everywhere else (pipelines/common/
# academic_periods.SEMESTER_LABELS).
OBSERVED_ACADEMIC_YEARS = [2021, 2022, 2023, 2024]
VALID_SEMESTER_NUMBERS = [1, 2]
VALID_GENDERS = ["Male", "Female"]
VALID_ADMISSION_TYPES = ["Freshman", "Transferee"]
VALID_PROGRAM_LEVELS = ["Bachelor", "Certificate", "Diploma"]


COLLEGE_SILVER_SCHEMA = DataFrameSchema(
    {
        "college_id": Column(_STR, Check.str_length(min_value=1), unique=True, nullable=False),
        "college_name": Column(_STR, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,  # audit columns (_ingested_at, ...) may still be present
)

PROGRAM_SILVER_SCHEMA = DataFrameSchema(
    {
        "program_id": Column(_STR, Check.str_length(min_value=1), unique=True, nullable=False),
        "program_name": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "program_level": Column(_STR, Check.isin(VALID_PROGRAM_LEVELS), nullable=False),
        "nominal_duration_years": Column(float, Check.in_range(0.5, 10), nullable=False),
    },
    strict=False,
)

STUDENT_SILVER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(_STR, Check.str_length(min_value=1), unique=True, nullable=False),
        "cohort_academic_year": Column(_INT, Check.isin(OBSERVED_ACADEMIC_YEARS), nullable=False),
        "gender": Column(_STR, Check.isin(VALID_GENDERS), nullable=False),
        "birth_year": Column(_INT, Check.in_range(1980, 2010), nullable=False),
        "home_province": Column(_STR, nullable=True),  # free text; genuinely optional
        "admission_type": Column(_STR, Check.isin(VALID_ADMISSION_TYPES), nullable=False),
        "entry_year_level": Column(_INT, Check.ge(1), nullable=False),
        "entry_college_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "entry_program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,
)

ENROLLMENT_SILVER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(_INT, Check.isin(OBSERVED_ACADEMIC_YEARS), nullable=False),
        "semester_number": Column(_INT, Check.isin(VALID_SEMESTER_NUMBERS), nullable=False),
        "college_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        # Post-cleaning this is either the 3-value controlled vocabulary
        # or an 'UNKNOWN:<raw>' tag -- business_rules.py / validate_and_
        # dedupe.py quarantine the latter. Restricting this to
        # isin(ENROLLED/GRADUATED/DROPPED) here would reject the very
        # rows those stages need to see in order to quarantine them.
        "enrollment_status": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "year_level": Column(_INT, Check.ge(1), nullable=False),
        "units_enrolled": Column(_INT, Check.ge(0), nullable=False),  # required metric
        "is_new_enrollee": Column(_BOOL, nullable=False),
    },
    strict=False,
)

GRADUATION_SILVER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(_INT, Check.isin(OBSERVED_ACADEMIC_YEARS), nullable=False),
        "semester_number": Column(_INT, Check.isin(VALID_SEMESTER_NUMBERS), nullable=False),
        "program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "years_to_complete": Column(float, Check.gt(0), nullable=False),  # required metric
    },
    strict=False,
)

DROPOUT_SILVER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(_INT, Check.isin(OBSERVED_ACADEMIC_YEARS), nullable=False),
        "semester_number": Column(_INT, Check.isin(VALID_SEMESTER_NUMBERS), nullable=False),
        "program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "dropout_reason": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "semesters_completed_before_dropout": Column(_INT, Check.ge(0), nullable=False),  # required metric
    },
    strict=False,
)

SHIFTER_SILVER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(_INT, Check.isin(OBSERVED_ACADEMIC_YEARS), nullable=False),
        "semester_number": Column(_INT, Check.isin(VALID_SEMESTER_NUMBERS), nullable=False),
        "from_program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
        "to_program_id": Column(_STR, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,
)

SILVER_SCHEMAS: Dict[str, DataFrameSchema] = {
    "college": COLLEGE_SILVER_SCHEMA,
    "program": PROGRAM_SILVER_SCHEMA,
    "student": STUDENT_SILVER_SCHEMA,
    "enrollment": ENROLLMENT_SILVER_SCHEMA,
    "graduation": GRADUATION_SILVER_SCHEMA,
    "dropout": DROPOUT_SILVER_SCHEMA,
    "shifter": SHIFTER_SILVER_SCHEMA,
}


def get_silver_schema(entity: str) -> DataFrameSchema:
    if entity not in SILVER_SCHEMAS:
        raise KeyError(f"No Silver schema defined for entity {entity!r}. Known entities: {sorted(SILVER_SCHEMAS)}")
    return SILVER_SCHEMAS[entity]


def validate_silver_dataframe(df: pd.DataFrame, entity: str) -> pd.DataFrame:
    """Validate `df` against `entity`'s Silver schema. lazy=True collects
    every violation across the whole DataFrame into one
    pandera.errors.SchemaErrors instead of stopping at the first row --
    essential for producing a usable failure report on a real-sized
    batch. Returns the validated DataFrame on success; raises
    SchemaErrors on failure (callers decide whether that's blocking or
    just logged -- see pipelines/silver/clean_entities.py's
    _run_silver_schema_validation for this project's non-blocking
    convention, matching Bronze's).
    """
    schema = get_silver_schema(entity)
    return schema.validate(df, lazy=True)


if __name__ == "__main__":
    for name, schema in SILVER_SCHEMAS.items():
        print(f"{name}: {len(schema.columns)} columns validated -> {sorted(schema.columns)}")