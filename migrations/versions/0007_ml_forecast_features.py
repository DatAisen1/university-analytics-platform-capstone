"""ML forecast feature tables (program-level + year-level enrollment)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/007_ml_forecast_features.sql. Two tables,
# not one: fact_graduation carries no year_level_key (a graduating student
# isn't recorded at a year level), so (college, program, year_level, period)
# only exists as a genuine grain for enrollment_count, not graduation_count.
_UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS gold.ml_program_forecast_features (
    college_key                     SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    program_key                     INTEGER  NOT NULL REFERENCES gold.dim_program (program_key),
    academic_period_key             SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    academic_year                   SMALLINT NOT NULL,
    semester_number                 SMALLINT NOT NULL,
    period_ordinal                  SMALLINT NOT NULL,

    enrollment_count                INTEGER NOT NULL,
    enrollment_count_lag_1          INTEGER,
    enrollment_count_lag_2          INTEGER,
    enrollment_count_rolling_avg_2  NUMERIC(10, 2),
    enrollment_count_historical_avg NUMERIC(10, 2),
    enrollment_count_trend          NUMERIC(12, 4),
    enrollment_count_seasonality    NUMERIC(10, 2),
    enrollment_count_growth         NUMERIC(10, 4),

    graduation_count                INTEGER NOT NULL,
    graduation_count_lag_1          INTEGER,
    graduation_count_lag_2          INTEGER,
    graduation_count_rolling_avg_2  NUMERIC(10, 2),
    graduation_count_historical_avg NUMERIC(10, 2),
    graduation_count_trend          NUMERIC(12, 4),
    graduation_count_seasonality    NUMERIC(10, 2),
    graduation_count_growth         NUMERIC(10, 4),

    PRIMARY KEY (college_key, program_key, academic_period_key)
);
CREATE INDEX IF NOT EXISTS ix_ml_program_features_period
    ON gold.ml_program_forecast_features (academic_period_key);

CREATE TABLE IF NOT EXISTS gold.ml_enrollment_features_by_year_level (
    college_key                     SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    program_key                     INTEGER  NOT NULL REFERENCES gold.dim_program (program_key),
    year_level_key                  SMALLINT NOT NULL REFERENCES gold.dim_year_level (year_level_key),
    academic_period_key             SMALLINT NOT NULL REFERENCES gold.dim_academic_period (academic_period_key),
    academic_year                   SMALLINT NOT NULL,
    semester_number                 SMALLINT NOT NULL,
    period_ordinal                  SMALLINT NOT NULL,

    enrollment_count                INTEGER NOT NULL,
    enrollment_count_lag_1          INTEGER,
    enrollment_count_lag_2          INTEGER,
    enrollment_count_rolling_avg_2  NUMERIC(10, 2),
    enrollment_count_historical_avg NUMERIC(10, 2),
    enrollment_count_trend          NUMERIC(12, 4),
    enrollment_count_seasonality    NUMERIC(10, 2),
    enrollment_count_growth         NUMERIC(10, 4),

    PRIMARY KEY (college_key, program_key, year_level_key, academic_period_key)
);
CREATE INDEX IF NOT EXISTS ix_ml_enrollment_by_year_level_period
    ON gold.ml_enrollment_features_by_year_level (academic_period_key);
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gold.ml_enrollment_features_by_year_level CASCADE;")
    op.execute("DROP TABLE IF EXISTS gold.ml_program_forecast_features CASCADE;")