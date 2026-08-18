"""Make model_registry.program_key / fact_forecast.program_key FKs deferrable

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-18
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# CONFIRMED bug, found running a `gold+` Dagster materialization against a
# real Postgres instance: `load_gold_to_postgres` (via
# pipelines.common.postgres.replace_all_table_contents) relies on EVERY
# FK constraint in the `gold`/`silver` schemas being DEFERRABLE INITIALLY
# DEFERRED, so `SET CONSTRAINTS ALL DEFERRED` lets it DELETE and
# re-INSERT dim_program (and every other dim/fact) inside one transaction
# without a mid-transaction FK violation -- see migration 0010's
# docstring for the full mechanism.
#
# Migration 0010 applied that treatment to every FK constraint that
# existed in `gold`/`silver` AT THE TIME IT RAN. Migration 0013 (three
# revisions later) added two brand-new FK constraints --
# model_registry.program_key and fact_forecast.program_key, both
# REFERENCES gold.dim_program (program_key) -- using a plain REFERENCES
# clause. Postgres FK constraints default to NOT DEFERRABLE unless
# declared otherwise, and 0013 never re-applied 0010's fix to its own new
# constraints. Net effect: the moment any row exists in
# gold.model_registry (i.e. after the first successful forecast
# deployment), `DELETE FROM gold."dim_program"` inside
# replace_all_table_contents's transaction fails immediately with
# `ForeignKeyViolation: ... still referenced from table "model_registry"`
# -- SET CONSTRAINTS ALL DEFERRED is a silent no-op for a constraint that
# was never marked deferrable, exactly as 0010's docstring warned could
# happen for a constraint added later and missed.
#
# Constraint names looked up dynamically via pg_constraint rather than
# hardcoded (e.g. "model_registry_program_key_fkey") -- migration 0013's
# own docstring already flagged this exact risk for its multi-column
# UNIQUE constraints (Postgres truncates auto-generated names past
# NAMEDATALEN); these two are short enough not to truncate today, but
# guessing is still the wrong default to teach in this codebase, and the
# dynamic lookup costs nothing. Scoped narrowly to FK constraints FROM
# gold.model_registry / gold.fact_forecast TO gold.dim_program
# specifically -- not a blanket re-sweep of every gold/silver FK the way
# 0010 did -- so this migration's effect (and its downgrade) stays
# limited to the two constraints it actually owns fixing.
#
# A new migration, not an edit to 0013: 0013 has already run in real
# environments, and editing a migration after it may have been applied
# doesn't change already-migrated databases and breaks Alembic's
# assumption that applied revisions are immutable history.
_UPGRADE_SQL = """
DO $$
DECLARE
    con RECORD;
BEGIN
    FOR con IN
        SELECT conname, conrelid::regclass AS table_name
        FROM pg_constraint
        WHERE contype = 'f'
          AND confrelid = 'gold.dim_program'::regclass
          AND conrelid = ANY (ARRAY['gold.model_registry', 'gold.fact_forecast']::regclass[])
    LOOP
        EXECUTE format(
            'ALTER TABLE %s ALTER CONSTRAINT %I DEFERRABLE INITIALLY DEFERRED',
            con.table_name, con.conname
        );
    END LOOP;
END $$;
"""

_DOWNGRADE_SQL = """
DO $$
DECLARE
    con RECORD;
BEGIN
    FOR con IN
        SELECT conname, conrelid::regclass AS table_name
        FROM pg_constraint
        WHERE contype = 'f'
          AND confrelid = 'gold.dim_program'::regclass
          AND conrelid = ANY (ARRAY['gold.model_registry', 'gold.fact_forecast']::regclass[])
    LOOP
        EXECUTE format(
            'ALTER TABLE %s ALTER CONSTRAINT %I NOT DEFERRABLE',
            con.table_name, con.conname
        );
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)