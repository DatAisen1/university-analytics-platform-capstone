"""Make silver- and gold-schema foreign keys DEFERRABLE INITIALLY DEFERRED

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/010_defer_gold_fk_constraints.sql.
#
# P0.51-54 (Idempotency and Reproducibility) root cause: pipelines.common.
# postgres.replace_table_contents does `TRUNCATE TABLE gold.<name>` one
# table at a time to rebuild the Gold layer in Postgres. Postgres refuses
# to TRUNCATE a table that has ANY foreign-key reference from another
# table -- structurally, based on the constraint's mere existence, NOT
# based on whether the referencing table currently has any rows -- unless
# every referencing table is truncated in that SAME command, or CASCADE
# is used (https://www.postgresql.org/docs/current/sql-truncate.html:
# "Checking validity in such cases would require table scans, and the
# whole point is not to do one.").
#
# Every gold dimension table here (dim_college, dim_program, ...) has at
# least one fact/ML/registry table referencing it, so
# `TRUNCATE TABLE gold.dim_college` fails with "cannot truncate a table
# referenced in a foreign key constraint" -- on the FIRST load, not just
# a rerun, since the FK constraints exist from migration 0003/0005/0007/
# 0008 onward, before any data is ever loaded. CASCADE isn't safe here
# either: dim_college is also referenced by gold.ml_program_forecast_
# features, gold.model_registry, and gold.fact_forecast -- tables owned
# by later, independent pipeline stages (features/training/forecast),
# not by load_gold_to_postgres. Cascading a warehouse reload's TRUNCATE
# into those would silently wipe ML feature history, the promoted-model
# registry, and forecast history every time Gold reloads.
#
# The standard, textbook Postgres fix for "reload a set of FK-connected
# tables without being able to insert in perfect dependency order" is to
# mark the FK constraints DEFERRABLE INITIALLY DEFERRED, then reload
# inside one transaction with `SET CONSTRAINTS ALL DEFERRED` -- deferred
# constraints are only checked at COMMIT, so a table can be emptied and
# repopulated mid-transaction while other tables still reference its old
# (soon-to-be-identical, since dim_dimensions.py assigns deterministic
# surrogate keys -- see P0.53 fix) rows, as long as everything is valid
# by the time the transaction commits. See
# pipelines/common/postgres.py's replace_all_table_contents for the
# transactional reload that relies on this.
#
# Scoped to every FK constraint DEFINED ON a table in the `gold` OR
# `silver` schema (queried from pg_constraint rather than hand-listing
# ~40 auto-generated constraint names, which would be one typo away from
# silently skipping a constraint) -- this covers every dim->dim,
# fact->dim, and ml_features/model_registry/fact_forecast->dim reference
# in `gold`, and every college->program->student->{enrollment,
# graduation, dropout, shifter} reference in `silver`, in one pass, and
# stays correct automatically if a future migration adds another.
#
# Bug found reviewing P0.51-54 (post-hoc, same session as the fix above):
# this migration's own FILENAME (0010_defer_silver_gold_fk_constraints)
# already promised both schemas, but the query body only ever scoped
# `connamespace = 'gold'::regnamespace` -- silver's FK constraints were
# never actually altered. That's not cosmetic: load_silver_to_postgres.py
# calls the exact same replace_all_table_contents() Gold uses, against
# silver.college <- silver.program/student <- silver.enrollment/
# graduation/dropout/shifter -- a real FK-connected chain. Without this,
# `SET CONSTRAINTS ALL DEFERRED` is a silent no-op for every silver
# constraint (Postgres only allows deferring a constraint that was
# declared/altered DEFERRABLE in the first place), so the DELETE-then-
# reinsert loop in replace_all_table_contents would fail immediately on
# `DELETE FROM silver.college` with a foreign key violation, the moment
# ANY row in silver.program/silver.student still referenced it -- i.e.
# on every load, not just a rerun. Confirmed against a live Postgres 16
# instance: silver's FK constraints all showed `condeferrable = f`
# before this fix.
_SCHEMAS = ("gold", "silver")

_UPGRADE_SQL = """
DO $$
DECLARE
    con RECORD;
BEGIN
    FOR con IN
        SELECT conname, conrelid::regclass AS table_name
        FROM pg_constraint
        WHERE contype = 'f'
          AND connamespace = ANY (ARRAY['gold', 'silver']::regnamespace[])
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
          AND connamespace = ANY (ARRAY['gold', 'silver']::regnamespace[])
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