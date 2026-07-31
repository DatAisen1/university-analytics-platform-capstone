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

import os
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from pipelines.common.postgres import get_admin_connection

_REPO_ROOT = Path(__file__).resolve().parents[2]

TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}


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
# ---------------------------------------------------------------------------

def test_load_gold_to_postgres_matches_parquet_row_counts(tmp_path):
    """Uses a scratch table name, never a real production table name --
    a test must not touch the same table the rest of the project's real
    data lives in, even in a 'shared' schema like gold."""
    from sqlalchemy import create_engine, text

    from pipelines.common.storage import LocalFileStorage
    from pipelines.gold.load_gold_to_postgres import load_gold_to_postgres

    scratch_table = "test_scratch_load_gold_college"
    gold_storage = LocalFileStorage(tmp_path / "gold_store")
    df = pd.DataFrame([{"college_key": 1, "college_id": "COA", "college_name": "College of Architecture"}])
    import io
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    gold_storage.write_bytes(f"gold/{scratch_table}/data.parquet", buf.getvalue())

    engine = create_engine(
        f"postgresql+psycopg2://uap_admin:local_dev_password@"
        f"{TEST_ENV['POSTGRES_HOST']}:{TEST_ENV['POSTGRES_PORT']}/{TEST_ENV['POSTGRES_DB']}"
    )
    try:
        counts = load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[scratch_table])
        assert counts == {scratch_table: 1}

        with engine.connect() as conn:
            result = conn.execute(text(f'SELECT COUNT(*) FROM gold."{scratch_table}"')).scalar()
            assert result == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS gold."{scratch_table}" CASCADE'))


def test_load_gold_to_postgres_survives_dependent_views_on_reload(tmp_path):
    """Regression test for the real bug found while building this: a
    naive if_exists='replace' does DROP TABLE + CREATE internally, which
    Postgres refuses once ANY view depends on the table."""
    from sqlalchemy import create_engine, text

    from pipelines.common.storage import LocalFileStorage
    from pipelines.gold.load_gold_to_postgres import load_gold_to_postgres

    scratch_table = "test_scratch_reload_with_view"
    gold_storage = LocalFileStorage(tmp_path / "gold_store")
    df = pd.DataFrame([{"id": 1, "value": "first_load"}])
    import io
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    gold_storage.write_bytes(f"gold/{scratch_table}/data.parquet", buf.getvalue())

    engine = create_engine(
        f"postgresql+psycopg2://uap_admin:local_dev_password@"
        f"{TEST_ENV['POSTGRES_HOST']}:{TEST_ENV['POSTGRES_PORT']}/{TEST_ENV['POSTGRES_DB']}"
    )
    try:
        load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[scratch_table])

        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE VIEW gold."{scratch_table}_view" AS SELECT * FROM gold."{scratch_table}"'
            ))

        df2 = pd.DataFrame([{"id": 1, "value": "second_load"}])
        buf2 = io.BytesIO()
        df2.to_parquet(buf2, engine="pyarrow", index=False)
        gold_storage.write_bytes(f"gold/{scratch_table}/data.parquet", buf2.getvalue())

        counts = load_gold_to_postgres(engine, gold_storage=gold_storage, tables=[scratch_table])
        assert counts == {scratch_table: 1}

        with engine.connect() as conn:
            value = conn.execute(text(f'SELECT value FROM gold."{scratch_table}"')).scalar()
            assert value == "second_load"
            view_value = conn.execute(text(f'SELECT value FROM gold."{scratch_table}_view"')).scalar()
            assert view_value == "second_load"
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP VIEW IF EXISTS gold."{scratch_table}_view" CASCADE'))
            conn.execute(text(f'DROP TABLE IF EXISTS gold."{scratch_table}" CASCADE'))


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
