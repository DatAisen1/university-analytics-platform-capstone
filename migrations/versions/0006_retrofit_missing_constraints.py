"""Guarded retrofit of constraints for pre-Alembic databases

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/006_retrofit_missing_constraints.sql.
# Databases deployed before 0003/0004 were wired into the migration runner
# may have Gold/Silver tables created implicitly by pandas
# `df.to_sql(if_exists='replace')`, which infers columns but adds no
# PRIMARY KEY / FOREIGN KEY / UNIQUE / NOT NULL. On a database that went
# through 0001-0005 in order this is a guaranteed no-op (every check is
# already satisfied) -- it exists purely as an idempotent safety net for
# any environment that predates this migration chain.
_UPGRADE_SQL = """
DO $$
DECLARE
    target RECORD;
BEGIN
    FOR target IN
        SELECT * FROM (VALUES
            ('gold', 'dim_program',  'uq_gold_dim_program_program_id',  'UNIQUE (program_id)'),
            ('gold', 'dim_college',  'uq_gold_dim_college_college_id',  'UNIQUE (college_id)'),
            ('gold', 'dim_student',  'uq_gold_dim_student_natural_key', NULL),
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_student_one_current
    ON gold.dim_student (student_id) WHERE _is_current;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    # Intentionally a no-op: this migration only adds constraints that
    # 0003/0004/0005 should already own. Dropping them here would let a
    # downgrade past 0006 silently strip constraints 0005 still expects
    # to exist while its own down_revision chain is being unwound.
    pass