-- warehouse/ddl/004_silver_star_schema.sql
--
-- Task 25 (Fix Database Constraints): explicit table definitions for the
-- `silver` schema. Until this migration, `silver` existed only as an
-- empty schema (created by 001_create_schemas.sql) with grants already
-- pointing at it (002_grants.sql grants pipeline_writer full read/write
-- on bronze/silver/gold) -- but no table, and therefore no constraint,
-- ever existed there. That's why a natural-key uniqueness constraint on
-- Silver's program table (uq_silver_programs_program_code in the
-- original audit finding; program_id is this project's current column
-- name for that natural key -- see pipelines/ingestion/ingest_to_bronze.py's
-- REQUIRED_COLUMNS and dbt/models/staging/_staging_schema.yml's existing
-- `program_id` uniqueness test, which tests the same invariant at the
-- dbt layer but nothing enforced it in the database itself) could not
-- exist: there was no table to attach it to.
--
-- pipelines/silver/load_silver_to_postgres.py loads into these tables
-- via the same TRUNCATE+append writer Gold uses
-- (pipelines.common.postgres.replace_table_contents), so re-running the
-- Silver-to-Postgres load is idempotent the same way Gold's load is.

CREATE TABLE IF NOT EXISTS silver.college (
    college_id    VARCHAR(16)  NOT NULL,
    college_name  VARCHAR(128) NOT NULL,
    CONSTRAINT pk_silver_college PRIMARY KEY (college_id)
);

CREATE TABLE IF NOT EXISTS silver.program (
    program_id              VARCHAR(32)  NOT NULL,
    program_name            VARCHAR(128) NOT NULL,
    college_id               VARCHAR(16)  NOT NULL,
    program_level            VARCHAR(16)  NOT NULL,
    nominal_duration_years   NUMERIC(3, 1) NOT NULL,
    CONSTRAINT pk_silver_program PRIMARY KEY (program_id),
    CONSTRAINT uq_silver_program_program_id UNIQUE (program_id),
    CONSTRAINT fk_silver_program_college FOREIGN KEY (college_id)
        REFERENCES silver.college (college_id)
);
CREATE INDEX IF NOT EXISTS ix_silver_program_college ON silver.program (college_id);

CREATE TABLE IF NOT EXISTS silver.student (
    student_id            VARCHAR(16) NOT NULL,
    cohort_academic_year   SMALLINT    NOT NULL,
    gender                 VARCHAR(16) NOT NULL,
    birth_year             SMALLINT    NOT NULL,
    home_province          VARCHAR(64) NOT NULL,
    admission_type         VARCHAR(16) NOT NULL,
    entry_year_level        SMALLINT    NOT NULL,
    entry_college_id        VARCHAR(16) NOT NULL,
    entry_program_id        VARCHAR(32) NOT NULL,
    CONSTRAINT pk_silver_student PRIMARY KEY (student_id),
    CONSTRAINT fk_silver_student_college FOREIGN KEY (entry_college_id)
        REFERENCES silver.college (college_id),
    CONSTRAINT fk_silver_student_program FOREIGN KEY (entry_program_id)
        REFERENCES silver.program (program_id)
);

CREATE TABLE IF NOT EXISTS silver.enrollment (
    student_id         VARCHAR(16) NOT NULL,
    academic_year        SMALLINT    NOT NULL,
    semester_number       SMALLINT    NOT NULL CHECK (semester_number IN (1, 2)),
    college_id            VARCHAR(16) NOT NULL,
    program_id            VARCHAR(32) NOT NULL,
    enrollment_status     VARCHAR(32) NOT NULL,
    year_level            SMALLINT    NOT NULL,
    units_enrolled        SMALLINT    NOT NULL,
    is_new_enrollee       BOOLEAN     NOT NULL,
    CONSTRAINT uq_silver_enrollment_student_period
        UNIQUE (student_id, academic_year, semester_number),
    CONSTRAINT fk_silver_enrollment_student FOREIGN KEY (student_id)
        REFERENCES silver.student (student_id),
    CONSTRAINT fk_silver_enrollment_program FOREIGN KEY (program_id)
        REFERENCES silver.program (program_id)
);
CREATE INDEX IF NOT EXISTS ix_silver_enrollment_student ON silver.enrollment (student_id);

CREATE TABLE IF NOT EXISTS silver.graduation (
    student_id          VARCHAR(16) NOT NULL,
    academic_year         SMALLINT    NOT NULL,
    semester_number        SMALLINT    NOT NULL CHECK (semester_number IN (1, 2)),
    program_id             VARCHAR(32) NOT NULL,
    college_id             VARCHAR(16) NOT NULL,
    years_to_complete      NUMERIC(4, 1) NOT NULL,
    CONSTRAINT uq_silver_graduation_student
        UNIQUE (student_id),
    CONSTRAINT fk_silver_graduation_student FOREIGN KEY (student_id)
        REFERENCES silver.student (student_id),
    CONSTRAINT fk_silver_graduation_program FOREIGN KEY (program_id)
        REFERENCES silver.program (program_id)
);

CREATE TABLE IF NOT EXISTS silver.dropout (
    student_id                          VARCHAR(16) NOT NULL,
    academic_year                        SMALLINT    NOT NULL,
    semester_number                       SMALLINT    NOT NULL CHECK (semester_number IN (1, 2)),
    program_id                            VARCHAR(32) NOT NULL,
    college_id                            VARCHAR(16) NOT NULL,
    dropout_reason                        VARCHAR(64) NOT NULL,
    semesters_completed_before_dropout    SMALLINT    NOT NULL,
    CONSTRAINT uq_silver_dropout_student
        UNIQUE (student_id),
    CONSTRAINT fk_silver_dropout_student FOREIGN KEY (student_id)
        REFERENCES silver.student (student_id),
    CONSTRAINT fk_silver_dropout_program FOREIGN KEY (program_id)
        REFERENCES silver.program (program_id)
);

CREATE TABLE IF NOT EXISTS silver.shifter (
    student_id         VARCHAR(16) NOT NULL,
    academic_year        SMALLINT    NOT NULL,
    semester_number       SMALLINT    NOT NULL CHECK (semester_number IN (1, 2)),
    from_program_id       VARCHAR(32) NOT NULL,
    to_program_id          VARCHAR(32) NOT NULL,
    CONSTRAINT uq_silver_shifter_student_period
        UNIQUE (student_id, academic_year, semester_number),
    CONSTRAINT fk_silver_shifter_student FOREIGN KEY (student_id)
        REFERENCES silver.student (student_id),
    CONSTRAINT fk_silver_shifter_from_program FOREIGN KEY (from_program_id)
        REFERENCES silver.program (program_id),
    CONSTRAINT fk_silver_shifter_to_program FOREIGN KEY (to_program_id)
        REFERENCES silver.program (program_id)
);