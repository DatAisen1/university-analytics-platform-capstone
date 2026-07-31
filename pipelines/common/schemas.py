"""
pipelines/common/schemas.py

Pandera schemas for each Bronze entity -- SHAPE validation only (are the
right columns present, with the right types, in sane ranges), run as a
post-write check immediately after Bronze ingestion (Day 8).

Deliberately NOT enforced here: business/controlled-vocabulary constraints
on text fields. The clearest example is `enrollment_status`: Day 6's noise
injection deliberately produced 9 realistic text variants (' ENROLLED ',
'Enrolled', 'DROPPED OUT', ...). If this schema restricted
enrollment_status to an isin(["ENROLLED", "GRADUATED", "DROPPED"]) check,
it would reject Bronze's own intentionally-realistic messy data --
exactly the data Silver's cleaning stage (Day 10) exists to normalize.
Bronze schema validation asks "is this shaped like enrollment data at
all?", not "is this clean?" -- conflating those two questions is a listed
common mistake in docs/13_Best_Practices.md, and this module is where
that principle actually gets enforced in code, not just described.
"""

from __future__ import annotations

from pandera.pandas import Column, DataFrameSchema, Check

from pipelines.common.academic_periods import academic_year_label

COLLEGE_SCHEMA = DataFrameSchema(
    {
        "college_id": Column(str, Check.str_length(min_value=1), unique=True, nullable=False),
        "college_name": Column(str, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,  # audit columns (_ingested_at etc.) are expected and fine
)

PROGRAM_SCHEMA = DataFrameSchema(
    {
        "program_id": Column(str, Check.str_length(min_value=1), unique=True, nullable=False),
        "program_name": Column(str, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "program_level": Column(str, Check.isin(["Bachelor", "Certificate", "Diploma"]), nullable=False),
        "nominal_duration_years": Column(float, Check.in_range(0.5, 10), nullable=False),
    },
    strict=False,
)

STUDENT_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(str, Check.str_length(min_value=1), unique=True, nullable=False),
        "cohort_academic_year": Column(str, Check.str_length(min_value=1), nullable=False),
        "gender": Column(str, nullable=False),
        "birth_year": Column(int, Check.in_range(1980, 2010), nullable=False),
        "home_province": Column(str, nullable=False),  # NOT value-checked -- Day 6 typo noise is expected here
        "admission_type": Column(str, Check.isin(["Freshman", "Transferee"]), nullable=False),
        "entry_year_level": Column(int, Check.ge(1), nullable=False),
        "entry_college_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "entry_program_id": Column(str, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,
)

ENROLLMENT_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(str, Check.str_length(min_value=1), nullable=False),
        "semester_number": Column(int, Check.isin([1, 2]), nullable=False),
        "college_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "program_id": Column(str, Check.str_length(min_value=1), nullable=False),
        # Intentionally NOT isin(["ENROLLED", "GRADUATED", "DROPPED"]) -- see module docstring.
        "enrollment_status": Column(str, Check.str_length(min_value=1), nullable=False),
        "year_level": Column(int, Check.ge(1), nullable=False),
        "units_enrolled": Column(int, Check.ge(0), nullable=False),
        "is_new_enrollee": Column(bool, nullable=False),
    },
    strict=False,
)

GRADUATION_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(str, Check.str_length(min_value=1), nullable=False),
        "semester_number": Column(int, Check.isin([1, 2]), nullable=False),
        "program_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "years_to_complete": Column(float, Check.gt(0), nullable=False),
    },
    strict=False,
)

DROPOUT_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(str, Check.str_length(min_value=1), nullable=False),
        "semester_number": Column(int, Check.isin([1, 2]), nullable=False),
        "program_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "college_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "dropout_reason": Column(str, Check.str_length(min_value=1), nullable=False),
        "semesters_completed_before_dropout": Column(int, Check.ge(0), nullable=False),
    },
    strict=False,
)

SHIFTER_SCHEMA = DataFrameSchema(
    {
        "student_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "academic_year": Column(str, Check.str_length(min_value=1), nullable=False),
        "semester_number": Column(int, Check.isin([1, 2]), nullable=False),
        "from_program_id": Column(str, Check.str_length(min_value=1), nullable=False),
        "to_program_id": Column(str, Check.str_length(min_value=1), nullable=False),
    },
    strict=False,
)

BRONZE_SCHEMAS = {
    "college": COLLEGE_SCHEMA,
    "program": PROGRAM_SCHEMA,
    "student": STUDENT_SCHEMA,
    "enrollment": ENROLLMENT_SCHEMA,
    "graduation": GRADUATION_SCHEMA,
    "dropout": DROPOUT_SCHEMA,
    "shifter": SHIFTER_SCHEMA,
}


def get_schema(entity: str) -> DataFrameSchema:
    if entity not in BRONZE_SCHEMAS:
        raise KeyError(f"No Bronze schema defined for entity {entity!r}. Known entities: {sorted(BRONZE_SCHEMAS)}")
    return BRONZE_SCHEMAS[entity]


def validate_bronze_dataframe(df, entity: str):
    """Validate `df` against the entity's schema. Returns the validated
    (possibly coerced) DataFrame on success. Raises
    pandera.errors.SchemaErrors (collecting ALL violations, not just the
    first) on failure -- callers should catch that, not a bare Exception,
    so a genuinely unexpected error doesn't get silently treated as 'just
    a schema problem.'
    """
    schema = get_schema(entity)
    return schema.validate(df, lazy=True)  # lazy=True: collect every violation, not just the first