-- warehouse/ddl/006_retrofit_missing_constraints.sql
--
-- Task 25: databases deployed BEFORE this fix may already have Gold
-- tables created the old way -- implicitly, via pandas
-- `df.to_sql(if_exists='replace')` in pipelines.common.postgres
-- .replace_table_contents(), before 003_gold_star_schema.sql was ever
-- wired into the migration runner. Those tables exist with the right
-- columns (pandas infers a reasonable type from the DataFrame) but with
-- NO primary key, foreign key, unique constraint, or NOT NULL --
-- exactly the missing-constraint symptom this task set out to fix
-- (e.g. uq_gold_dim_program_program_id never existing).
--
-- 003/004/005 use plain `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ...
-- ADD CONSTRAINT`, which do the right thing on a CLEAN database but
-- will error with "constraint already exists" or "relation does not
-- exist" if run a second time or against a table that was already
-- created without them. This migration is the guarded, idempotent
-- retrofit path: for every constraint 003/004/005 should have created,
-- check pg_constraint first and only add it if it's actually missing.
--
-- Safe to run against: a brand-new database (every check is false, all
-- ADDs skipped because 003/004/005 already added them), a legacy
-- database missing constraints (every relevant ADD fires once), or a
-- database that's already been retrofitted (every check is true,
-- nothing happens).

DO $$
DECLARE
    target RECORD;
BEGIN
    FOR target IN
        SELECT * FROM (VALUES
            ('gold', 'dim_program',  'uq_gold_dim_program_program_id',  'UNIQUE (program_id)'),
            ('gold', 'dim_college',  'uq_gold_dim_college_college_id',  'UNIQUE (college_id)'),
            ('gold', 'dim_student',  'uq_gold_dim_student_natural_key', NULL),  -- handled separately: partial index, not a plain UNIQUE
            ('silver', 'program',    'uq_silver_program_program_id',    'UNIQUE (program_id)'),
            ('silver', 'college',    'pk_silver_college',               'PRIMARY KEY (college_id)')
        ) AS t(schema_name, table_name, constraint_name, constraint_def)
    LOOP
        CONTINUE WHEN target.constraint_def IS NULL;

        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = target.schema_name AND table_name = target.table_name
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = target.schema_name AND c.conname = target.constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
                target.schema_name, target.table_name, target.constraint_name, target.constraint_def
            );
            RAISE NOTICE 'Retrofitted missing constraint % on %.%',
                target.constraint_name, target.schema_name, target.table_name;
        END IF;
    END LOOP;
END $$;

-- gold.dim_student's "exactly one current row per student" invariant is a
-- partial unique index (only meaningful WHERE _is_current), which the
-- VALUES-table pattern above can't express generically -- retrofit it
-- directly, guarded the same way via IF NOT EXISTS (natively supported
-- for indexes, unlike named constraints).
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_student_one_current
    ON gold.dim_student (student_id) WHERE _is_current;