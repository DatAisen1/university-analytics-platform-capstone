"""Grant pipeline_writer USAGE on gold-schema sequences

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-14
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Bug found running the forecast asset end-to-end for the first time:
# gold.fact_forecast (created by 0008_forecast_registry.py) has a
# BIGSERIAL primary key, which Postgres backs with an implicit SEQUENCE
# object (fact_forecast_fact_forecast_key_seq). 002_grants.sql's
# pipeline_writer grants are scoped entirely to TABLES --
# `GRANT ALL ON ALL TABLES IN SCHEMA bronze, silver, gold` and
# `ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT ALL ON TABLES` -- and
# neither of those covers SEQUENCEs, which Postgres treats as a
# separate grantable object type. Every earlier table pipeline_writer
# wrote to either predates this gap being exercised or doesn't use a
# SERIAL/BIGSERIAL PK, so this went unnoticed until the first real
# INSERT into fact_forecast: confirmed against a live Postgres 16
# instance as `psycopg2.errors.InsufficientPrivilege: permission
# denied for sequence fact_forecast_fact_forecast_key_seq`.
#
# Fixed two ways, both needed:
#   1. Explicit USAGE, SELECT on every sequence that already exists in
#      gold today (fact_forecast's and model_registry's), so this
#      migration fixes the current database immediately.
#   2. ALTER DEFAULT PRIVILEGES for sequences, scoped to pipeline_writer
#      as the creating role (mirroring 002_grants.sql's existing TABLES
#      default-privilege pattern), so any future gold table with a
#      SERIAL/BIGSERIAL PK doesn't silently reintroduce this same bug.
_UPGRADE_SQL = """
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gold TO pipeline_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_writer IN SCHEMA gold
    GRANT USAGE, SELECT ON SEQUENCES TO pipeline_writer;
"""

_DOWNGRADE_SQL = """
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_writer IN SCHEMA gold
    REVOKE USAGE, SELECT ON SEQUENCES FROM pipeline_writer;
REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gold FROM pipeline_writer;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)