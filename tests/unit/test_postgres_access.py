"""
tests/unit/test_postgres_access.py

These tests connect to a REAL PostgreSQL instance as each of the four
service roles and prove Day 15's access-control guarantees are enforced
by Postgres itself, not just documented as a convention. This is only
possible because a real Postgres server is running in this environment
(installed directly, since Docker isn't available here -- see
docs/06_Data_Warehouse.md Section 6); the schema/role/grant DDL is
identical to what `docker compose up` would give a user on their own
machine.

Requires a Postgres instance reachable via the POSTGRES_* environment
variables, with the warehouse already bootstrapped
(pipelines.common.postgres.bootstrap_warehouse). Skipped automatically
if no Postgres connection is available, so the rest of the suite
doesn't become dependent on a database being up.
"""

import os

import psycopg2
import pytest

from pipelines.common.postgres import bootstrap_warehouse, get_admin_connection, get_role_connection

TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}

ROLE_PASSWORDS = {
    "pipeline_writer": "pw_pipeline123",
    "dbt_role": "pw_dbt123",
    "dashboard_reader": "pw_dash123",
    "analyst_readonly": "pw_analyst123",
}


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="No reachable Postgres instance for these tests -- see module docstring"
)


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_warehouse():
    """Ensure the warehouse (roles, schemas, grants) exists before any
    test in this module runs, and create one throwaway table per layer
    so read/write tests have something real to hit."""
    bootstrap_warehouse(ROLE_PASSWORDS, env=TEST_ENV)

    admin_conn = get_admin_connection(TEST_ENV)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        for schema in ("bronze", "silver", "gold", "marts"):
            cur.execute(f"DROP TABLE IF EXISTS {schema}.test_table")
            cur.execute(f"CREATE TABLE {schema}.test_table (id INT, name TEXT)")
            cur.execute(f"INSERT INTO {schema}.test_table VALUES (1, 'test')")
    admin_conn.close()

    yield

    admin_conn = get_admin_connection(TEST_ENV)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        for schema in ("bronze", "silver", "gold", "marts"):
            cur.execute(f"DROP TABLE IF EXISTS {schema}.test_table")
    admin_conn.close()


def _role_conn(role: str):
    return get_role_connection(role, ROLE_PASSWORDS[role], env=TEST_ENV)


# ---------------------------------------------------------------------------
# dashboard_reader -- the core Day 15 guarantee
# ---------------------------------------------------------------------------

def test_dashboard_reader_can_read_gold():
    conn = _role_conn("dashboard_reader")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gold.test_table")
        assert cur.fetchall() == [(1, "test")]
    conn.close()


def test_dashboard_reader_can_read_marts():
    conn = _role_conn("dashboard_reader")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM marts.test_table")
        assert cur.fetchall() == [(1, "test")]
    conn.close()


def test_dashboard_reader_cannot_read_silver():
    conn = _role_conn("dashboard_reader")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM silver.test_table")
    conn.close()


def test_dashboard_reader_cannot_read_bronze():
    conn = _role_conn("dashboard_reader")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM bronze.test_table")
    conn.close()


def test_dashboard_reader_cannot_write_to_gold():
    conn = _role_conn("dashboard_reader")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO gold.test_table VALUES (2, 'hacked')")
    conn.close()


def test_dashboard_reader_cannot_write_to_marts():
    conn = _role_conn("dashboard_reader")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO marts.test_table VALUES (2, 'hacked')")
    conn.close()


def test_dashboard_reader_cannot_write_to_bronze_or_silver():
    conn = _role_conn("dashboard_reader")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO bronze.test_table VALUES (2, 'hacked')")
    conn.close()


# ---------------------------------------------------------------------------
# analyst_readonly -- narrower than dashboard_reader (marts only)
# ---------------------------------------------------------------------------

def test_analyst_readonly_can_read_marts():
    conn = _role_conn("analyst_readonly")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM marts.test_table")
        assert cur.fetchall() == [(1, "test")]
    conn.close()


def test_analyst_readonly_cannot_read_gold():
    """The narrower role: unlike dashboard_reader, analyst_readonly has no
    access to raw Gold facts/dimensions -- marts only."""
    conn = _role_conn("analyst_readonly")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gold.test_table")
    conn.close()


# ---------------------------------------------------------------------------
# pipeline_writer -- full read/write on bronze/silver/gold
# ---------------------------------------------------------------------------

def test_pipeline_writer_can_write_to_all_three_layers():
    conn = _role_conn("pipeline_writer")
    conn.autocommit = True
    with conn.cursor() as cur:
        for schema in ("bronze", "silver", "gold"):
            cur.execute(f"INSERT INTO {schema}.test_table VALUES (99, 'pipeline_writer_test')")
            cur.execute(f"DELETE FROM {schema}.test_table WHERE id = 99")  # clean up after itself
    conn.close()  # no exception raised == success


def test_pipeline_writer_cannot_write_to_marts():
    """pipeline_writer's job is Bronze/Silver/Gold -- marts belongs to dbt_role."""
    conn = _role_conn("pipeline_writer")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO marts.test_table VALUES (2, 'wrong role')")
    conn.close()


# ---------------------------------------------------------------------------
# dbt_role -- read Gold, read/write marts
# ---------------------------------------------------------------------------

def test_dbt_role_can_read_gold():
    conn = _role_conn("dbt_role")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gold.test_table")
        assert cur.fetchall() == [(1, "test")]
    conn.close()


def test_dbt_role_can_write_to_marts():
    conn = _role_conn("dbt_role")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO marts.test_table VALUES (98, 'dbt_role_test')")
        cur.execute("DELETE FROM marts.test_table WHERE id = 98")
    conn.close()


def test_dbt_role_cannot_write_to_gold():
    """dbt_role only READS Gold -- it builds marts FROM Gold, never
    writes back into it."""
    conn = _role_conn("dbt_role")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO gold.test_table VALUES (2, 'wrong direction')")
    conn.close()


def test_dbt_role_cannot_read_bronze_or_silver():
    conn = _role_conn("dbt_role")
    conn.autocommit = True
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM silver.test_table")
    conn.close()


# ---------------------------------------------------------------------------
# Default privileges on FUTURE tables (not just the ones that existed at grant time)
# ---------------------------------------------------------------------------

def test_dashboard_reader_can_read_a_table_created_after_grants_were_set(bootstrapped_warehouse):
    """The real production scenario: pipeline_writer creates a NEW Gold
    table (not one that existed when GRANT was first run) -- dashboard_reader
    must still be able to read it, via the ALTER DEFAULT PRIVILEGES FOR
    ROLE pipeline_writer clause in 002_grants.sql."""
    writer_conn = _role_conn("pipeline_writer")
    writer_conn.autocommit = True
    with writer_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS gold.future_table")
        cur.execute("CREATE TABLE gold.future_table (id INT)")
        cur.execute("INSERT INTO gold.future_table VALUES (42)")
    writer_conn.close()

    reader_conn = _role_conn("dashboard_reader")
    with reader_conn.cursor() as cur:
        cur.execute("SELECT * FROM gold.future_table")
        assert cur.fetchall() == [(42,)]
    reader_conn.close()

    # cleanup
    admin_conn = get_admin_connection(TEST_ENV)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS gold.future_table")
    admin_conn.close()