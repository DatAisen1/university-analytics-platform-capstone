-- warehouse/ddl/005_gold_fact_constraints.sql
--
-- Task 26 (Prevent Duplicate Pipeline Runs): 003_gold_star_schema.sql's
-- fact tables have no PRIMARY KEY or UNIQUE constraint at all. Gold
-- facts are deliberately fully rebuilt from Silver on every pipeline run
-- (see pipelines/gold/build_facts.py's module docstring) and loaded via
-- TRUNCATE + append (pipelines.common.postgres.replace_table_contents),
-- which is correct and idempotent AS LONG AS the DataFrame handed to the
-- loader never contains duplicate rows at the fact's natural grain. That
-- assumption was previously enforced only in Python (build_facts.py's
-- join logic) with nothing backing it up at the database layer -- a
-- regression there would silently double-count enrollment/graduation/
-- dropout/retention numbers with no error raised anywhere.
--
-- These constraints make each fact table's grain a database-enforced
-- invariant, matching what build_facts.py already assumes:
--   fact_enrollment / fact_dropout / fact_shifter / fact_retention:
--       one row per (student_key, academic_period_key)
--   fact_graduation:
--       one row per student (a student graduates at most once)

ALTER TABLE gold.fact_enrollment
    ADD CONSTRAINT uq_gold_fact_enrollment_student_period
        UNIQUE (student_key, academic_period_key);

ALTER TABLE gold.fact_graduation
    ADD CONSTRAINT uq_gold_fact_graduation_student
        UNIQUE (student_key);

ALTER TABLE gold.fact_dropout
    ADD CONSTRAINT uq_gold_fact_dropout_student
        UNIQUE (student_key);

ALTER TABLE gold.fact_shifter
    ADD CONSTRAINT uq_gold_fact_shifter_student_period
        UNIQUE (student_key, academic_period_key);

ALTER TABLE gold.fact_retention
    ADD CONSTRAINT uq_gold_fact_retention_student_period
        UNIQUE (student_key, academic_period_key);