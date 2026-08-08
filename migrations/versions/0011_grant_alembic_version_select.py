"""Grant pipeline_writer SELECT on public.alembic_version

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Bug found reviewing P0.51-54 (same session as 0010's silver/gold fix):
# pipelines.common.migrations.assert_up_to_date() -- the hard guard both
# load_gold_to_postgres.py and load_silver_to_postgres.py run before
# writing a single row -- reads public.alembic_version through the SAME
# connection those loaders use to write data, i.e. authenticated as
# pipeline_writer, never as the admin role that actually ran the
# migrations. `alembic_version` lives in `public` (Alembic's own
# bookkeeping table, created there regardless of this project's
# bronze/silver/gold/marts schema layout) and 002_grants.sql's
# per-schema GRANT statements never touch `public` at all -- so
# pipeline_writer had no SELECT privilege on it. Once the previous bug in
# this same file (_engine_for_connection unwrapping a pooled connection
# proxy) is fixed, THIS is the next thing every load hits: a real,
# correctly-labeled `psycopg2.errors.InsufficientPrivilege: permission
# denied for table alembic_version` -- confirmed against a live Postgres
# 16 instance. Scoped to SELECT only: pipeline_writer reads migration
# state to fail loudly on a stale schema, it must never be able to write
# to Alembic's own version-tracking table.
# Bug found reviewing P0.51-54 (post-hoc, same session as the fix above):
# pipelines.common.migrations.assert_up_to_date() -- the hard guard both
# load_gold_to_postgres.py and load_silver_to_postgres.py run before
# writing a single row -- reads public.alembic_version through the SAME
# connection those loaders use to write data, i.e. authenticated as
# pipeline_writer, never as the admin role that actually ran the
# migrations. `alembic_version` lives in `public` (Alembic's own
# bookkeeping table, created there regardless of this project's
# bronze/silver/gold/marts schema layout) and 002_grants.sql's
# per-schema GRANT statements never touch `public` at all -- so
# pipeline_writer had no SELECT privilege on it. Once the previous bug in
# this same file (_engine_for_connection unwrapping a pooled connection
# proxy) is fixed, THIS is the next thing every load hits: a real,
# correctly-labeled `psycopg2.errors.InsufficientPrivilege: permission
# denied for table alembic_version` -- confirmed against a live Postgres
# 16 instance. Scoped to SELECT only: pipeline_writer reads migration
# state to fail loudly on a stale schema, it must never be able to write
# to Alembic's own version-tracking table.
#
# GRANT SELECT alone is not sufficient, though -- also grant USAGE on
# the `public` schema itself, explicitly, rather than relying on
# Postgres's ambient "GRANT USAGE ON SCHEMA public TO PUBLIC", which:
#   (a) only exists because initdb/template1 sets it up once, so it is
#       silently GONE the moment anyone runs `DROP SCHEMA public CASCADE`
#       + `CREATE SCHEMA public` to truly reset a database (confirmed:
#       this broke assert_up_to_date with a confusing "relation
#       alembic_version does not exist" -- UndefinedTable, not
#       InsufficientPrivilege, because missing schema USAGE blocks name
#       resolution before Postgres even gets to checking table-level
#       SELECT), and
#   (b) is routinely revoked outright as a security hardening step in
#       real deployments (`REVOKE ALL ON SCHEMA public FROM PUBLIC` is
#       a common Postgres hardening baseline).
# A migration that depends on an ambient default it doesn't itself
# grant is one hardening pass or one `DROP SCHEMA public CASCADE` away
# from silently breaking every Gold/Silver load again. Grant it here,
# explicitly, so this migration is self-sufficient regardless of what
# the `public` schema's default privileges happen to be.
_UPGRADE_SQL = """
GRANT USAGE ON SCHEMA public TO pipeline_writer;
GRANT SELECT ON public.alembic_version TO pipeline_writer;
"""

_DOWNGRADE_SQL = """
REVOKE SELECT ON public.alembic_version FROM pipeline_writer;
REVOKE USAGE ON SCHEMA public FROM pipeline_writer;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)