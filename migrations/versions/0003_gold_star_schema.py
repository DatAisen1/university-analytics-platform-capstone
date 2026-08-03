"""Gold star schema: dims + facts

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/003_gold_star_schema.sql -- see that
# file's original comments (preserved in git history) for full rationale.
_UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS gold.dim_academic_period (
    academic_period_key SMALLINT PRIMARY KEY,
    academic_year        SMALLINT NOT NULL,
    semester_number       SMALLINT NOT NULL CHECK (semester_number IN (1, 2)),
    year_label            VARCHAR(16) NOT NULL,
    semester_label         VARCHAR(16) NOT NULL,
    period_label           VARCHAR(32) NOT NULL,
    period_ordinal         SMALLINT NOT NULL,
    UNIQUE (academic_year, semester_number),
    UNIQUE (period_ordinal)
);

CREATE TABLE IF NOT EXISTS gold.dim_calendar (
    date_key             INTEGER PRIMARY KEY,
    full_date            DATE NOT NULL UNIQUE,
    year                 SMALLINT NOT NULL,
    quarter              SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month                SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day                  SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    is_semester_start    BOOLEAN NOT NULL,
    is_semester_end      BOOLEAN NOT NULL,
    academic_period_key  SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key)
);
CREATE INDEX IF NOT EXISTS ix_dim_calendar_period ON gold.dim_calendar (academic_period_key);

CREATE TABLE IF NOT EXISTS gold.dim_year_level (
    year_level_key    SMALLINT PRIMARY KEY,
    year_level        SMALLINT NOT NULL UNIQUE,
    year_level_label  VARCHAR(32) NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_gender (
    gender_key    SMALLINT PRIMARY KEY,
    gender_code   VARCHAR(16) NOT NULL UNIQUE,
    gender_label  VARCHAR(16) NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_college (
    college_key    SMALLINT PRIMARY KEY,
    college_id     VARCHAR(16) NOT NULL UNIQUE,
    college_name   VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_program (
    program_key             INTEGER PRIMARY KEY,
    program_id              VARCHAR(32) NOT NULL UNIQUE,
    program_name            VARCHAR(128) NOT NULL,
    college_id               VARCHAR(16) NOT NULL,
    program_level            VARCHAR(16) NOT NULL,
    nominal_duration_years   NUMERIC(3, 1) NOT NULL,
    college_key              SMALLINT NOT NULL REFERENCES gold.dim_college (college_key)
);
CREATE INDEX IF NOT EXISTS ix_dim_program_college ON gold.dim_program (college_key);

CREATE TABLE IF NOT EXISTS gold.dim_student (
    student_key            INTEGER PRIMARY KEY,
    student_id              VARCHAR(16) NOT NULL,
    gender_key               SMALLINT NOT NULL REFERENCES gold.dim_gender (gender_key),
    birth_year               SMALLINT NOT NULL,
    home_province            VARCHAR(64) NOT NULL,
    admission_type           VARCHAR(16) NOT NULL,
    college_key              SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    program_key              INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    _valid_from_period_key    SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    _valid_to_period_key      SMALLINT REFERENCES gold.dim_academic_period (academic_period_key),
    _is_current               BOOLEAN NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_student_one_current
    ON gold.dim_student (student_id) WHERE _is_current;
CREATE INDEX IF NOT EXISTS ix_dim_student_natural_key ON gold.dim_student (student_id);

CREATE TABLE IF NOT EXISTS gold.fact_enrollment (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student (student_key),
    program_key             INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    college_key             SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    academic_period_key      SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    enrollment_status        VARCHAR(16) NOT NULL,
    year_level_key            SMALLINT NOT NULL REFERENCES gold.dim_year_level (year_level_key),
    units_enrolled            SMALLINT NOT NULL,
    is_new_enrollee           BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fact_enrollment_period ON gold.fact_enrollment (college_key, academic_period_key);
CREATE INDEX IF NOT EXISTS ix_fact_enrollment_student ON gold.fact_enrollment (student_key);

CREATE TABLE IF NOT EXISTS gold.fact_graduation (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student (student_key),
    program_key             INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    college_key             SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    academic_period_key      SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    years_to_complete         NUMERIC(4, 1) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fact_graduation_period ON gold.fact_graduation (college_key, academic_period_key);

CREATE TABLE IF NOT EXISTS gold.fact_dropout (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student (student_key),
    program_key             INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    college_key             SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    academic_period_key      SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    dropout_reason            VARCHAR(32) NOT NULL,
    semesters_completed_before_dropout SMALLINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fact_dropout_period ON gold.fact_dropout (college_key, academic_period_key);

CREATE TABLE IF NOT EXISTS gold.fact_shifter (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student (student_key),
    from_program_key         INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    to_program_key            INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    academic_period_key       SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key)
);
CREATE INDEX IF NOT EXISTS ix_fact_shifter_period ON gold.fact_shifter (academic_period_key);

CREATE TABLE IF NOT EXISTS gold.fact_retention (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student (student_key),
    program_key             INTEGER NOT NULL REFERENCES gold.dim_program (program_key),
    college_key             SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    academic_period_key      SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    is_retained               SMALLINT NOT NULL CHECK (is_retained IN (0, 1))
);
CREATE INDEX IF NOT EXISTS ix_fact_retention_period ON gold.fact_retention (college_key, academic_period_key);

CREATE TABLE IF NOT EXISTS gold.fact_institution_kpi (
    college_key                    SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    academic_period_key             SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    enrollment_count                 INTEGER NOT NULL,
    graduation_count                 INTEGER NOT NULL,
    dropout_count                    INTEGER NOT NULL,
    shifter_count                    INTEGER NOT NULL,
    retention_rate                    NUMERIC(6, 5) NOT NULL,
    graduation_rate                   NUMERIC(6, 5) NOT NULL,
    dropout_rate                      NUMERIC(6, 5) NOT NULL,
    shifter_stability                 NUMERIC(6, 5) NOT NULL,
    enrollment_stability              NUMERIC(6, 5) NOT NULL,
    program_completion_momentum       NUMERIC(6, 5) NOT NULL,
    success_rate                      NUMERIC(5, 1) NOT NULL,
    PRIMARY KEY (college_key, academic_period_key)
);
CREATE INDEX IF NOT EXISTS ix_fact_institution_kpi_period ON gold.fact_institution_kpi (academic_period_key);
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    for table in (
        "fact_institution_kpi", "fact_retention", "fact_shifter", "fact_dropout",
        "fact_graduation", "fact_enrollment", "dim_student", "dim_program",
        "dim_college", "dim_gender", "dim_year_level", "dim_calendar", "dim_academic_period",
    ):
        op.execute(f"DROP TABLE IF EXISTS gold.{table} CASCADE;")