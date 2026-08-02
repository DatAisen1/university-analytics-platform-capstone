"""
pipelines/common/postgres.py

Real PostgreSQL connectivity for the warehouse -- Days 15+. Unlike
Bronze/Silver/Gold (which use DuckDB/Parquet as documented local
stand-ins because they run in-process, no server needed -- see
docs/03_Data_Engineering.md Section 13), the warehouse genuinely needs a
running Postgres server, and one now exists in this environment (see
docs/06_Data_Warehouse.md Section 6 -- installed directly via apt for
real testing, distinct from the Docker Compose setup documented for a
user's own machine, but targeting the identical schema/DDL either way).

Role bootstrap is split from the grants themselves for a reason: role
CREATION needs a password (a secret, never committed), while GRANTs are
pure structure (safe to version control in warehouse/ddl/002_grants.sql).
Mixing them would force secrets into a version-controlled file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
from pipelines.common.errors import PostgresError
import psycopg2

from pipelines.common.config import ConfigError

_REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = _REPO_ROOT / "warehouse" / "ddl"

SERVICE_ROLES = ["pipeline_writer", "dbt_role", "dashboard_reader", "analyst_readonly"]


def get_admin_connection(env: Optional[dict] = None):
    """Connect as the admin/superuser role (POSTGRES_USER), the only role
    allowed to create schemas/roles/grants."""
    env = env if env is not None else os.environ
    required = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s) for Postgres admin connection: {missing}")

    try:
        return psycopg2.connect(
            host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"], dbname=env["POSTGRES_DB"],
            user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"],
        )
    except psycopg2.OperationalError as exc:
        raise PostgresError(
            f"Failed to connect to Postgres as admin: {exc}", stage="Postgres Connection",
        ) from exc


def get_role_connection(role: str, password: str, env: Optional[dict] = None):
    """Connect AS a specific service role -- used both by pipeline code
    (each stage connects as its own role, never as admin) and by tests
    that need to prove a role's permissions are actually enforced by
    Postgres itself, not just documented."""
    if role not in SERVICE_ROLES:
        raise ValueError(f"Unknown role {role!r}. Known roles: {SERVICE_ROLES}")
    env = env if env is not None else os.environ
    required = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s) for Postgres connection: {missing}")

    return psycopg2.connect(
        host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"], dbname=env["POSTGRES_DB"],
        user=role, password=password,
    )


def bootstrap_roles(admin_conn, passwords: Dict[str, str]) -> None:
    """Create the four service roles if they don't already exist, with
    passwords supplied by the caller (sourced from environment variables,
    never hardcoded). Idempotent: safe to run against a database that
    already has some or all of these roles.
    """
    missing = set(SERVICE_ROLES) - set(passwords)
    if missing:
        raise ConfigError(f"Missing password(s) for role(s): {sorted(missing)}")

    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        for role in SERVICE_ROLES:
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = %(role)s) THEN
                        EXECUTE format('CREATE ROLE %%I LOGIN PASSWORD %%L', %(role)s, %(password)s);
                    END IF;
                END
                $$;
                """,
                {"role": role, "password": passwords[role]},
            )


def apply_schema_ddl(admin_conn) -> List[str]:
    """Run every migration in warehouse/ddl/ that hasn't been applied yet,
    in numeric order (Task 25 fix). Assumes roles already exist
    (bootstrap_roles must run first -- 002_grants.sql references role
    names that must already be valid).

    Previously this hardcoded exactly ["001_create_schemas.sql",
    "002_grants.sql"], which meant 003_gold_star_schema.sql (and any
    later constraint-defining file) was silently never executed --
    the root cause of missing constraints such as
    uq_gold_dim_program_program_id / uq_silver_program_program_id (see
    pipelines/common/migrations.py's module docstring for the full
    story). Now every file under warehouse/ddl/ is a tracked migration
    and this function is the single entry point that applies all of
    them, idempotently.
    """
    from pipelines.common.migrations import apply_migrations

    return apply_migrations(admin_conn, ddl_dir=DDL_DIR)


def bootstrap_warehouse(passwords: Dict[str, str], env: Optional[dict] = None) -> List[str]:
    """Full warehouse bootstrap: create roles, then apply every migration
    (schemas, grants, Gold DDL, Silver DDL, constraint retrofits, ...).
    Idempotent -- safe to re-run against an already-bootstrapped database
    (Task 26/27: this is exactly what makes 'run migrations again' safe
    on every deploy, not just the first one).
    """
    conn = get_admin_connection(env)
    try:
        bootstrap_roles(conn, passwords)
        return apply_schema_ddl(conn)
    finally:
        conn.close()


class MissingTableError(PostgresError):
    """Raised when a Gold/Silver writer targets a table that doesn't
    exist yet -- migrations haven't been applied. Now a PostgresError
    subclass (Task 46): category=POSTGRES_ERROR, and callers can attach
    rows_affected (the row count of the DataFrame that couldn't load)."""

def replace_table_contents(engine, schema: str, table_name: str, df) -> None:
    """Write `df` into `schema.table_name`, replacing its entire contents.

    Uses TRUNCATE + append -- NOT pandas' `if_exists='replace'` (DROP
    TABLE + CREATE), because Postgres refuses to DROP a table with
    dependent views (e.g. a dbt staging view built on top of it), so a
    naive replace breaks the very next reload after the first `dbt run`.

    Task 25 fix: the table must already exist (created by a tracked
    migration in warehouse/ddl/, with its full set of constraints) --
    this function will NOT fall back to creating it via `to_sql`
    anymore. A missing table is now a loud MissingTableError, not a
    silent constraint-less table. Run
    `pipelines.common.migrations.apply_migrations()` (or the warehouse
    bootstrap) before calling this.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names(schema=schema))

    # AFTER
    if table_name not in existing_tables:
        raise MissingTableError(
            f"{schema}.{table_name} does not exist. Apply migrations "
            f"(pipelines.common.migrations.apply_migrations) before loading data -- "
            f"tables must be created by tracked DDL, with their full constraints, "
            f"never implicitly by pandas.to_sql.",
            stage="Postgres Load",
            entity=f"{schema}.{table_name}",
            rows_affected=len(df),
        )

    with engine.begin() as conn:
        conn.execute(text(f'TRUNCATE TABLE {schema}."{table_name}"'))
    df.to_sql(table_name, engine, schema=schema, if_exists="append", index=False)