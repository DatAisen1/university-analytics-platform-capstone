-- warehouse/ddl/007_ml_forecast_features.sql
--
-- Task 31: DDL for the two ML feature tables build_ml_features.py
-- produces. Required before pipelines.common.postgres.replace_table_contents
-- will accept writes to them (Task 25's guardrail: no implicit table
-- creation via pandas.to_sql). Run AFTER 003_gold_star_schema.sql.
--
-- Two tables, not one, because the two target metrics don't share a
-- common finest grain: fact_graduation carries no year_level_key (a
-- graduating student isn't recorded at a year level), so a genuine
-- (college, program, year_level, period) grain only exists for
-- enrollment_count.

-- Grain: one row per (college, program, academic_period). Covers both
-- enrollment_count and graduation_count -- the finest grain BOTH source
-- facts support in common.
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

-- Grain: one row per (college, program, year_level, academic_period).
-- Enrollment-only, per the note above.
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