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
import sys
from pathlib import Path

import pytest

from pipelines.common.migrations import apply_migrations
from pipelines.common.postgres import bootstrap_warehouse, get_admin_connection
from models.forecasting.train_prophet import MCMC_DISABLED_REASON

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _pg_test_db import create_isolated_database, drop_database_if_exists  # noqa: E402

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

# P1 fix (architecture, not a typo): this module previously bootstrapped
# straight into TEST_ENV["POSTGRES_DB"] -- the SAME database name
# test_dbt_marts.py / test_train_prophet.py rely on already containing
# real, populated pipeline output. bootstrap_warehouse() applying
# migrations against a database that's supposed to hold real data (or
# another destructive test module's fresh, empty rebuild) is exactly the
# collision that produced cascading "alembic_version says head but
# schemas are missing" errors across the whole suite. Constraint-existence
# checks only need a genuinely fresh, correctly migrated database -- they
# have no need to touch anything real -- so this module gets its own
# throwaway database via tests/_pg_test_db.py, same pattern as
# test_warehouse_rebuild_from_clean.py.
_ISOLATED_DB_BASE = f"{TEST_ENV['POSTGRES_DB']}_constraints_test"
ISOLATED_ENV: dict = {}


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
    exact sequence a clean-database deploy would follow -- against this
    module's own private database (see _ISOLATED_DB_BASE above)."""
    global ISOLATED_ENV
    db_name = create_isolated_database(_ISOLATED_DB_BASE, TEST_ENV)
    ISOLATED_ENV = {**TEST_ENV, "POSTGRES_DB": db_name}
    bootstrap_warehouse(ROLE_PASSWORDS, env=ISOLATED_ENV)
    yield
    drop_database_if_exists(db_name, TEST_ENV)


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
    """Alembic is now the sole migration authority (P0.6): its own
    alembic_version table is the source of truth for what has been
    applied, not the old meta.schema_migrations checksum table this
    project used before Alembic replaced it (see
    pipelines/common/migrations.py's module docstring). This test was
    updated to check the authoritative mechanism rather than the
    superseded one -- checking meta.schema_migrations would fail on
    every correctly migrated database, since that table is no longer
    created at all.

    Bug fix: this used to assert `current_head == "0009"` -- a literal
    revision id. That's a maintenance trap disguised as a regression
    test: it silently goes stale (and starts failing on an otherwise-
    correct database) every time a new migration is added, which is
    exactly what happened when migrations 0010 and 0011 were added in
    this same P0.51-54 pass. Compare against Alembic's own script
    directory heads instead -- the actual "is this database current"
    question -- so the test keeps testing the right thing as the
    migration chain grows, the same principle assert_up_to_date()
    already follows in application code.
    """
    from alembic.script import ScriptDirectory

    from pipelines.common.migrations import _alembic_config

    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        current_head = cur.fetchone()[0]
    conn.close()

    expected_heads = set(ScriptDirectory.from_config(_alembic_config()).get_heads())
    assert {current_head} == expected_heads, (
        f"expected Alembic head(s) {expected_heads}, database is at {current_head!r} "
        f"-- migrations 0004-0011 (Silver/Gold constraints, ML/forecast, "
        f"deferred FKs, alembic_version grant) never ran, or a newer "
        f"migration hasn't been applied yet."
    )


def test_apply_migrations_twice_is_a_noop():
    """Task 26/27: re-running migrations against an already-migrated
    database must not error and must not re-apply anything."""
    conn = get_admin_connection(ISOLATED_ENV)
    newly_applied = apply_migrations(conn)
    conn.close()
    assert newly_applied == []


# ---------------------------------------------------------------------------
# Gold: the constraints originally missing
# ---------------------------------------------------------------------------

def test_gold_dim_program_has_natural_key_unique_constraint():
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "gold", "dim_program", "uq_gold_dim_program_program_id")
    conn.close()


def test_gold_dim_program_has_college_foreign_key():
    conn = get_admin_connection(ISOLATED_ENV)
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
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        for column in ("student_id", "gender_key", "college_key", "program_key", "_is_current"):
            assert _column_not_null(cur, "gold", "dim_student", column), f"dim_student.{column} should be NOT NULL"
    conn.close()


def test_gold_dim_student_one_current_row_index():
    conn = get_admin_connection(ISOLATED_ENV)
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
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "gold", fact_table, constraint_name)
    conn.close()


# ---------------------------------------------------------------------------
# Silver: previously had no table DDL at all
# ---------------------------------------------------------------------------

def test_silver_program_table_exists_with_unique_constraint():
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'silver' AND table_name = 'program'"
        )
        assert cur.fetchone() is not None, "silver.program does not exist"
        assert _constraint_exists(cur, "silver", "program", "uq_silver_program_program_id")
    conn.close()


def test_silver_enrollment_has_student_period_unique_constraint():
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        assert _constraint_exists(cur, "silver", "enrollment", "uq_silver_enrollment_student_period")
    conn.close()


def test_silver_tables_have_not_null_natural_keys():
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        assert _column_not_null(cur, "silver", "student", "student_id")
        assert _column_not_null(cur, "silver", "program", "program_id")
    conn.close()


# ---------------------------------------------------------------------------
# gold.fact_forecast.interval_calibration_note (migrations 0018 -> 0019):
# regression coverage for a real production bug. 0018 shipped this column
# as VARCHAR(256), sized without measuring the real strings it has to
# hold; MCMC_DISABLED_REASON (train_prophet.py) is 389 characters and
# fires on essentially every Prophet-champion forecast, since
# MCMC_CALIBRATION_ENABLED = False is the default -- not a rare edge
# case. Every forecast write for a Prophet champion hit
# psycopg2.errors.StringDataRightTruncation at the Forecast Deployment
# pipeline stage in a real `dagster job execute` run, caught by no test
# in this suite until now. 0019 widened the column to TEXT; these two
# tests are what should have existed before 0018 shipped.
# ---------------------------------------------------------------------------

def test_gold_fact_forecast_interval_calibration_columns_are_sized_correctly():
    """Column-metadata check: would have caught the original VARCHAR(256)
    sizing bug directly, without needing to know the specific string that
    eventually broke production. Asserts the note column is genuinely
    TEXT (character_maximum_length IS NULL) rather than merely "not 256"
    -- a regression back to some OTHER too-small N would fail this test
    exactly the same way the original bug should have.

    A stronger version of this test would INSERT the real
    MCMC_DISABLED_REASON string end-to-end through gold.fact_forecast and
    gold.model_registry and assert an exact round-trip -- deliberately
    not attempted here, because gold.model_registry's full current NOT
    NULL / column surface (mae, rmse, best_baseline_mae, beats_baseline,
    is_champion, rejected_reason, program_key, ... accumulated across
    migrations 0008, 0009, 0013, 0014) is exactly the kind of thing this
    project's own guardrails say to verify against the real schema
    rather than reconstruct from memory, and doing that reconstruction
    correctly without a live database to check it against risked shipping
    a second bug while fixing the first. The column-metadata check below
    is the version of this regression test that's actually verified
    correct, not merely plausible-looking."""
    conn = get_admin_connection(ISOLATED_ENV)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'gold' AND table_name = 'fact_forecast'
              AND column_name IN ('interval_calibration_method', 'interval_calibration_note')
            """
        )
        columns = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    conn.close()

    # interval_calibration_method is a real, bounded 4-value enum (see
    # migration 0018's CHECK constraint) -- VARCHAR(32) was never the bug.
    assert columns["interval_calibration_method"] == ("character varying", 32)

    # interval_calibration_note is genuinely free-text diagnostic content
    # -- TEXT, unbounded, is the actual fix, not a larger arbitrary cap.
    assert columns["interval_calibration_note"] == ("text", None)

    # Directly proves the specific real string that broke production
    # would now fit, without needing a full cross-table INSERT to do it:
    # PostgreSQL has no length limit on TEXT, so this is really just
    # confirming MCMC_DISABLED_REASON hasn't itself grown into something
    # pathological (e.g. accidentally duplicated) since this test was
    # written, imported live rather than hardcoded so it can't drift.
    assert len(MCMC_DISABLED_REASON) == 389