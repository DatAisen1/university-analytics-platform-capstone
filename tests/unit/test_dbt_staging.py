"""
tests/unit/test_dbt_staging.py

Tests for pipelines/gold/load_gold_to_postgres.py (real Postgres required)
and, if the `dbt` CLI is available, a subprocess-level integration check
that `dbt run` and `dbt test` actually succeed against the live warehouse
-- Day 16's validation checklist ("dbt run succeeds with zero errors")
and testing checklist ("dbt test on staging models"), captured as an
automated test rather than only a manually-run CLI command.

Skipped automatically if Postgres or the dbt CLI isn't available, for the
same reason test_postgres_access.py skips: the rest of the suite should
never become contingent on external services being up.
"""

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from pipelines.common.postgres import bootstrap_warehouse, get_admin_connection

_REPO_ROOT = Path(__file__).resolve().parents[2]

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _pg_test_db import create_isolated_database, drop_database_if_exists  # noqa: E402

_ISOLATED_DB_BASE = f"{TEST_ENV['POSTGRES_DB']}_load_gold_test"


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


def _dbt_available() -> bool:
    return shutil.which("dbt") is not None


pytestmark = pytest.mark.skipif(
    not (_postgres_available() and _dbt_available()),
    reason="Requires both a reachable Postgres instance and the dbt CLI -- see module docstring",
)


# ---------------------------------------------------------------------------
# load_gold_to_postgres
#
# P1 fix (contract mismatch, not a typo): load_gold_to_postgres deliberately
# refuses to load into any table that isn't already created by tracked
# DDL/migrations (MissingTableError, see replace_all_table_contents's
# docstring in pipelines/common/postgres.py -- "never implicitly by
# pandas.to_sql", a deliberate earlier fix). These tests used to target an
# invented scratch table name (e.g. "test_scratch_load_gold_college") that
# no migration ever creates, which made every run fail with MissingTableError
# regardless of whether load_gold_to_postgres itself works correctly --
# testing a contract the function was never meant to support.
#
# Fix: give this module the same isolated-throwaway-database pattern
# test_build_ml_features.py already established (see _pg_test_db.py's
# docstring for why: two destructive test modules must never share one
# mutable database name). Inside that isolated DB, bootstrap_warehouse runs
# the real tracked migrations, so a real migrated table (gold.dim_college)
# exists to load into -- exercising load_gold_to_postgres's actual contract
# instead of a name it was designed to reject.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def isolated_db():
    db_name = create_isolated_database(_ISOLATED_DB_BASE, TEST_ENV)
    env = {**TEST_ENV, "POSTGRES_DB": db_name}
    bootstrap_warehouse(ROLE_PASSWORDS, env=env)
    yield env
    drop_database_if_exists(db_name, TEST_ENV)


def _pipeline_writer_engine(env):
    from sqlalchemy import create_engine

    return create_engine(
        f"postgresql+psycopg2://pipeline_writer:{ROLE_PASSWORDS['pipeline_writer']}@"
        f"{env['POSTGRES_HOST']}:{env['POSTGRES_PORT']}/{env['POSTGRES_DB']}"
    )


def test_load_gold_to_postgres_matches_parquet_row_counts(tmp_path, isolated_db):
    """Loads into gold.dim_college -- a real, migration-created table --
    inside this module's throwaway database, so this never touches (or
    even shares a schema with) any table real project data lives in."""
    from sqlalchemy import text

    from pipelines.common.storage import LocalFileStorage
    from pipelines.gold.load_gold_to_postgres import load_gold_to_postgres

    table = "dim_college"
    gold_storage = LocalFileStorage(tmp_path / "gold_store")
    df = pd.DataFrame([{"college_key": 1, "college_id": "COA", "college_name": "College of Architecture"}])
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    gold_storage.write_bytes(f"gold/{table}/data.parquet", buf.getvalue())

    engine = _pipeline_writer_engine(isolated_db)
    counts = load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[table])
    assert counts == {table: 1}

    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT COUNT(*) FROM gold."{table}"')).scalar()
        assert result == 1


def test_load_gold_to_postgres_survives_dependent_views_on_reload(tmp_path, isolated_db):
    """Regression test for the real bug found while building this: a
    naive if_exists='replace' does DROP TABLE + CREATE internally, which
    Postgres refuses once ANY view depends on the table. Uses gold.dim_college
    (a real migrated table) for the same reason as the test above."""
    from sqlalchemy import text

    from pipelines.common.storage import LocalFileStorage
    from pipelines.gold.load_gold_to_postgres import load_gold_to_postgres

    table = "dim_college"
    gold_storage = LocalFileStorage(tmp_path / "gold_store")
    df = pd.DataFrame([{"college_key": 1, "college_id": "COA", "college_name": "first_load"}])
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    gold_storage.write_bytes(f"gold/{table}/data.parquet", buf.getvalue())

    engine = _pipeline_writer_engine(isolated_db)
    try:
        load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[table])

        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE VIEW gold."{table}_view" AS SELECT * FROM gold."{table}"'
            ))

        df2 = pd.DataFrame([{"college_key": 1, "college_id": "COA", "college_name": "second_load"}])
        buf2 = io.BytesIO()
        df2.to_parquet(buf2, engine="pyarrow", index=False)
        gold_storage.write_bytes(f"gold/{table}/data.parquet", buf2.getvalue())

        counts = load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[table])
        assert counts == {table: 1}

        with engine.connect() as conn:
            value = conn.execute(text(f'SELECT college_name FROM gold."{table}"')).scalar()
            assert value == "second_load"
            view_value = conn.execute(text(f'SELECT college_name FROM gold."{table}_view"')).scalar()
            assert view_value == "second_load"
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP VIEW IF EXISTS gold."{table}_view" CASCADE'))


# ---------------------------------------------------------------------------
# dbt run / dbt test -- subprocess-level integration check
# ---------------------------------------------------------------------------

def _run_dbt(*args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "DBT_PROFILES_DIR": str(_REPO_ROOT / "dbt"),
        "POSTGRES_HOST": TEST_ENV["POSTGRES_HOST"],
        "POSTGRES_PORT": TEST_ENV["POSTGRES_PORT"],
        "POSTGRES_DB": TEST_ENV["POSTGRES_DB"],
        "DBT_ROLE_PASSWORD": os.environ.get("TEST_DBT_ROLE_PASSWORD", "pw_dbt123"),
    })
    return subprocess.run(
        ["dbt", *args, "--project-dir", str(_REPO_ROOT / "dbt")],
        capture_output=True, text=True, env=env,
    )


def test_dbt_run_succeeds_with_zero_errors():
    """Day 16's validation checklist, run as an actual automated check."""
    result = _run_dbt("run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ERROR=0" in result.stdout


def test_dbt_test_passes_all_staging_tests():
    """Day 16's testing checklist: dbt test on staging models."""
    result = _run_dbt("test")
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"PASS=(\d+) WARN=(\d+) ERROR=(\d+)", result.stdout)
    assert match, f"Could not find dbt summary line in output: {result.stdout}"
    passed, warned, errored = (int(g) for g in match.groups())
    assert errored == 0
    assert passed > 0