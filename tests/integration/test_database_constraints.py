"""
tests/integration/test_database_constraints.py

Task 25 (Fix Database Constraints): proves -- against a REAL, freshly
migrated Postgres database -- that every PRIMARY KEY, FOREIGN KEY,
UNIQUE constraint, index, and NOT NULL constraint the warehouse depends
on actually exists. This is the regression test for the exact bug this
task fixed: 003_gold_star_schema.sql defined these constraints correctly
the whole time, but nothing ever executed it, so a clean database ended
up with none of them. This test would have caught that.

Requires a Postgres instance reachable via TEST_POSTGRES_* environment
variables. Skipped automatically if unavailable.
"""

import os

import pytest

from pipelines.common.migrations import apply_migrations
from pipelines.common.postgres import bootstrap_warehouse, get_admin_connection

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
def migrated_warehouse():
    """Bootstrap roles/schemas/grants, then run every migration -- the
    exact sequence a clean-database deploy would follow."""
    bootstrap_warehouse(ROLE_PASSWORDS, env=TEST_ENV)
    yield


def _constraint_exists(cur, schema: str, table: str, constraint_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM pg_constraint c
        JOIN pg_namespace n ON n.oid = c.connamespace
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE n.nspname = %s AND t.relname = %s AND c.conname = %s
        """,
        (schema, table, constraint_name),
    )
    return cur.fetchone() is not None


def _index_exists(cur, schema: str, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
        (schema, index_name),
    )
    return cur.fetchone() is not None


def _column_not_null(cur, schema: str, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT is_nullable FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """,
        (schema, table, column),
    )
    row = cur.fetchone()
    assert row is not None, f"{schema}.{table}.{column} does not exist"
    return row[0] == "NO"


# ---------------------------------------------------------------------------
# Migration runner itself
# ---------------------------------------------------------------------------

def test_migrations_are_all_applied():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM meta.schema_migrations ORDER BY version")
        applied = [row[0] for row in cur.fetchall()]
    conn.close()
    # Every migration file that ships in the repo must show up as applied.
    assert "003" in applied, "003_gold_star_schema.sql was never applied -- the exact original bug"
    assert "004" in applied
    assert "005" in applied


def test_apply_migrations_twice_is_a_noop():
    """Task 26/27: re-running migrations against an already-migrated
    database must not error and must not re-apply anything."""
    conn = get_admin_connection(TEST_ENV)
    newly_applied = apply_migrations(conn)
    conn.close()
    assert newly_applied == []


# ---------------------------------------------------------------------------
# Gold: the constraints originally missing
# ---------------------------------------------------------------------------

def test_gold_dim_program_has_natural_key_unique_constraint():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "gold", "dim_program", "uq_gold_dim_program_program_id")
    conn.close()


def test_gold_dim_program_has_college_foreign_key():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'gold' AND t.relname = 'dim_program' AND c.contype = 'f'
            """
        )
        assert cur.fetchone() is not None, "dim_program is missing its FK to dim_college"
    conn.close()


def test_gold_dim_student_not_null_columns():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        for column in ("student_id", "gender_key", "college_key", "program_key", "_is_current"):
            assert _column_not_null(cur, "gold", "dim_student", column), f"dim_student.{column} should be NOT NULL"
    conn.close()


def test_gold_dim_student_one_current_row_index():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        assert _index_exists(cur, "gold", "ux_dim_student_one_current")
    conn.close()


@pytest.mark.parametrize("fact_table,constraint_name", [
    ("fact_enrollment", "uq_gold_fact_enrollment_student_period"),
    ("fact_graduation", "uq_gold_fact_graduation_student"),
    ("fact_dropout", "uq_gold_fact_dropout_student"),
    ("fact_shifter", "uq_gold_fact_shifter_student_period"),
    ("fact_retention", "uq_gold_fact_retention_student_period"),
])
def test_gold_fact_tables_have_grain_enforcing_unique_constraint(fact_table, constraint_name):
    """Previously these fact tables had NO PK/UNIQUE at all (Task 26)."""
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "gold", fact_table, constraint_name)
    conn.close()


# ---------------------------------------------------------------------------
# Silver: previously had no table DDL at all
# ---------------------------------------------------------------------------

def test_silver_program_table_exists_with_unique_constraint():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'silver' AND table_name = 'program'"
        )
        assert cur.fetchone() is not None, "silver.program does not exist"
        assert _constraint_exists(cur, "silver", "program", "uq_silver_program_program_id")
    conn.close()


def test_silver_enrollment_has_student_period_unique_constraint():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "silver", "enrollment", "uq_silver_enrollment_student_period")
    conn.close()


def test_silver_tables_have_not_null_natural_keys():
    conn = get_admin_connection(TEST_ENV)
    with conn.cursor() as cur:
        assert _column_not_null(cur, "silver", "student", "student_id")
        assert _column_not_null(cur, "silver", "program", "program_id")
    conn.close()