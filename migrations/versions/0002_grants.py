"""Per-layer role grants (pipeline_writer, dbt_role, dashboard_reader, analyst_readonly)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Ported verbatim from warehouse/ddl/002_grants.sql. Precondition unchanged
# from the original: the four SERVICE_ROLES (pipelines/common/postgres.py::
# bootstrap_roles) must already exist -- run bootstrap_roles() BEFORE
# `alembic upgrade head`, same ordering the original apply_schema_ddl() required.
_UPGRADE_SQL = """
GRANT USAGE, CREATE ON SCHEMA bronze, silver, gold TO pipeline_writer;
GRANT ALL ON ALL TABLES IN SCHEMA bronze, silver, gold TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT ALL ON TABLES TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT ALL ON TABLES TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT ALL ON TABLES TO pipeline_writer;

GRANT USAGE ON SCHEMA gold TO dbt_role;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO dbt_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO dbt_role;

GRANT USAGE, CREATE ON SCHEMA marts TO dbt_role;
GRANT ALL ON ALL TABLES IN SCHEMA marts TO dbt_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT ALL ON TABLES TO dbt_role;

GRANT USAGE ON SCHEMA gold, marts TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA gold, marts TO dashboard_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO dashboard_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO dashboard_reader;

GRANT USAGE ON SCHEMA marts TO analyst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO analyst_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO analyst_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_writer IN SCHEMA gold
    GRANT SELECT ON TABLES TO dbt_role, dashboard_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE dbt_role IN SCHEMA marts
    GRANT SELECT ON TABLES TO dashboard_reader, analyst_readonly;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA bronze, silver, gold, marts FROM pipeline_writer, dbt_role, dashboard_reader, analyst_readonly;")
    op.execute("REVOKE ALL ON SCHEMA bronze, silver, gold, marts FROM pipeline_writer, dbt_role, dashboard_reader, analyst_readonly;")