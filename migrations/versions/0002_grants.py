"""Per-layer role grants (pipeline_writer, dbt_role, dashboard_reader, analyst_readonly)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

P2 fix: this migration used to assume the four SERVICE_ROLES already
existed, created earlier by pipelines.common.postgres.bootstrap_roles()
-- which is only ever called from `python -m pipelines.common.postgres`,
NOT from Alembic itself. Running the standard, expected `alembic upgrade
head` against a genuinely fresh database (no prior bootstrap_roles()
call) therefore failed here with "role pipeline_writer does not exist"
-- a footgun for any engineer or CI job that reaches for the ordinary
Alembic entrypoint instead of this project's wrapper.

Fix: this migration now creates the roles itself, idempotently, as its
first step, using the same SERVICE_ROLES list bootstrap_roles() uses
(imported, not retyped, so the four role names have one source of
truth) via a CREATE-ROLE-IF-NOT-EXISTS DO block equivalent to
bootstrap_roles()'s own SQL.

Why this does NOT simply call bootstrap_roles(op.get_bind().connection,
...) directly: bootstrap_roles() sets `conn.autocommit = True` before
running, which is correct for its normal standalone call path (a fresh
psycopg2 connection with nothing else happening on it) but WRONG here --
Alembic already has this connection inside its own migration
transaction (env.py's `context.begin_transaction()`), and flipping
autocommit mid-transaction is undefined/unsafe territory for psycopg2.
CREATE ROLE, unlike CREATE DATABASE, is fully transactional in
PostgreSQL, so it needs no autocommit at all -- running it as an
ordinary `op.execute()` statement inside Alembic's existing transaction
is both simpler and safer than fighting the connection's transaction
state. The role *names* are still imported from
pipelines.common.postgres.SERVICE_ROLES so there is exactly one place
that lists what the four roles are called, even though the two code
paths (this migration, and bootstrap_roles() for other programmatic
callers) each run their own equivalent SQL to create them.

Role passwords are read from the same PIPELINE_WRITER_PASSWORD /
DBT_ROLE_PASSWORD / DASHBOARD_READER_PASSWORD / ANALYST_READONLY_PASSWORD
environment variables bootstrap_roles() always required -- unset the
same way it was always required to be set, just enforced one layer
earlier now (at `alembic upgrade head` time, with a clear error message,
instead of failing later on the GRANT statements below with a confusing
"role does not exist"). This makes the bare CLI self-sufficient:
`alembic upgrade head` against a brand-new database now succeeds on its
own, no separate wrapper script required first.

Bug fix: this originally read the four passwords via a bare
os.environ.get()/os.environ[...] -- which, like migrations/env.py's
former ALEMBIC_DATABASE_URL handling (same root cause, found and fixed
separately), never loads .env at all, only real process-environment
variables. A developer with a perfectly correct .env would still hit
"Missing environment variable(s)" here unless they'd manually exported
every value into their shell first. Now reads through
pipelines.common.settings.get_postgres_settings().service_role_passwords()
-- the SAME centralized, .env-aware, validated config layer
bootstrap_roles() itself already uses (pipelines/common/postgres.py) --
so a value present in .env is found here exactly as it is everywhere
else in this codebase, with one source of truth for "where do these
four passwords come from" instead of two.
"""

from alembic import op
from sqlalchemy import text

from pipelines.common.postgres import SERVICE_ROLES
from pipelines.common.settings import SettingsError, get_postgres_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Keys must match pipelines.common.postgres.SERVICE_ROLES exactly --
# asserted at import time below so the two lists can never silently
# drift apart.
_ROLE_PASSWORD_ENV_VARS = {
    "pipeline_writer": "PIPELINE_WRITER_PASSWORD",
    "dbt_role": "DBT_ROLE_PASSWORD",
    "dashboard_reader": "DASHBOARD_READER_PASSWORD",
    "analyst_readonly": "ANALYST_READONLY_PASSWORD",
}
assert set(_ROLE_PASSWORD_ENV_VARS) == set(SERVICE_ROLES), (
    f"_ROLE_PASSWORD_ENV_VARS {sorted(_ROLE_PASSWORD_ENV_VARS)} has drifted "
    f"from pipelines.common.postgres.SERVICE_ROLES {sorted(SERVICE_ROLES)}"
)


def _create_roles_if_missing() -> None:
    """Idempotently create the four service roles inside Alembic's own
    migration transaction (CREATE ROLE is transactional in Postgres, so
    this is safe to run here -- see the module docstring for why this
    does NOT reuse bootstrap_roles() directly)."""
    passwords = get_postgres_settings().service_role_passwords()
    missing_env_vars = sorted(
        env_var for role, env_var in _ROLE_PASSWORD_ENV_VARS.items()
        if not passwords.get(role)
    )
    if missing_env_vars:
        raise SettingsError(
            "Migration 0002_grants needs the four service-role passwords "
            "to create pipeline_writer/dbt_role/dashboard_reader/"
            f"analyst_readonly before granting to them. Missing environment "
            f"variable(s): {missing_env_vars}. Set these in .env "
            f"(see .env.example), then re-run `alembic upgrade head`."
        )

    bind = op.get_bind()
    for role, env_var in _ROLE_PASSWORD_ENV_VARS.items():
        bind.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = :role) THEN
                        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :role, :password);
                    END IF;
                END
                $$;
                """
            ),
            {"role": role, "password": passwords[role]},
        )


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
    _create_roles_if_missing()
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA bronze, silver, gold, marts FROM pipeline_writer, dbt_role, dashboard_reader, analyst_readonly;")
    op.execute("REVOKE ALL ON SCHEMA bronze, silver, gold, marts FROM pipeline_writer, dbt_role, dashboard_reader, analyst_readonly;")