"""
tests/unit/test_dbt_marts.py

Tests for Day 17's marts: a subprocess-level check that `dbt test`
passes with the marts in place, plus direct SQL sanity checks against
the real warehouse confirming the marts' derived arithmetic is actually
correct, not just "the query ran without error."

Skipped automatically if Postgres or the dbt CLI isn't reachable, same
as test_dbt_staging.py.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipelines.common.postgres import get_admin_connection, get_role_connection

_REPO_ROOT = Path(__file__).resolve().parents[2]

TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}
DBT_ROLE_PASSWORD = os.environ.get("TEST_DBT_ROLE_PASSWORD", "pw_dbt123")


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


def _dbt_available() -> bool:
    return shutil.which("dbt") is not None


def _marts_exist() -> bool:
    if not _postgres_available():
        return False
    try:
        conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM marts.mart_executive_summary")
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_postgres_available() and _dbt_available()),
    reason="Requires both a reachable Postgres instance and the dbt CLI -- see module docstring",
)


def _run_dbt(*args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "DBT_PROFILES_DIR": str(_REPO_ROOT / "dbt"),
        "POSTGRES_HOST": TEST_ENV["POSTGRES_HOST"],
        "POSTGRES_PORT": TEST_ENV["POSTGRES_PORT"],
        "POSTGRES_DB": TEST_ENV["POSTGRES_DB"],
        "DBT_ROLE_PASSWORD": DBT_ROLE_PASSWORD,
    })
    return subprocess.run(
        ["dbt", *args, "--project-dir", str(_REPO_ROOT / "dbt")],
        capture_output=True, text=True, env=env,
    )


def test_dbt_run_builds_all_seven_marts_with_zero_errors():
    # 5 original marts + mart_college_trend + mart_program_trend (P2 --
    # Trend Analysis). Bumped from ">= 5" alongside those two additions.
    result = _run_dbt("run", "--select", "path:models/marts")
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"Done\. PASS=(\d+)", result.stdout)
    assert match and int(match.group(1)) >= 7


def test_dbt_test_passes_for_marts():
    result = _run_dbt("test", "--select", "path:models/marts")
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"ERROR=(\d+)", result.stdout)
    assert match and int(match.group(1)) == 0


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_executive_summary_has_one_row_per_semester():
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT academic_period_key) FROM marts.mart_executive_summary")
        total, distinct = cur.fetchone()
    conn.close()
    # 10-period canonical grain (5 academic years x 2 semesters, 2021-2025
    # extended dataset) -- see pipelines/gold/build_dimensions.py's
    # ACADEMIC_YEARS. Verified against a real fresh pipeline run, not
    # assumed from the config: SELECT COUNT(*), COUNT(DISTINCT
    # academic_period_key) FROM marts.mart_executive_summary returned
    # 10, 10 (previously 6, 6 under the 2021-2023 window this test was
    # originally written against).
    assert total == distinct == 10


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_executive_summary_total_enrollment_matches_gold():
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("SELECT SUM(total_enrollment) FROM marts.mart_executive_summary")
        mart_total = cur.fetchone()[0]
    conn.close()

    admin_conn = get_admin_connection(TEST_ENV)
    with admin_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM gold.fact_enrollment")
        gold_total = cur.fetchone()[0]
    admin_conn.close()

    # Not pinned to a specific row count: the whole point of this test is
    # that the mart's aggregation always matches its source, regardless of
    # which dataset config generated it. A hardcoded number here (this used
    # to assert `== 32701`) silently breaks every time someone regenerates
    # data with different volumes -- even when the pipeline is working
    # correctly, like right now (current deterministic generator run
    # produces 20109, and mart_total == gold_total == 20109 confirms the
    # aggregation is correct for whatever the current dataset is).
    assert mart_total == gold_total


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_college_performance_benchmark_arithmetic_is_internally_consistent():
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_college_performance
            WHERE ABS(institutional_success_index_vs_campus_avg - (institutional_success_index - campus_avg_institutional_success_index)) > 0.001
        """)
        inconsistent_count = cur.fetchone()[0]
    conn.close()
    assert inconsistent_count == 0


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_retention_risk_flag_matches_its_own_stated_definition():
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_retention_risk
            WHERE is_declining_two_consecutive_semesters
              != (retention_rate < retention_rate_prev1 AND retention_rate_prev1 < retention_rate_prev2)
        """)
        mismatch_count = cur.fetchone()[0]
    conn.close()
    assert mismatch_count == 0


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_trend_marts_delta_arithmetic_is_internally_consistent():
    # semester_over_semester_abs_delta is DEFINED as metric_value -
    # prior_period_value -- this catches a regression where the two
    # drift apart (e.g. someone edits one but not the other).
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_college_trend
            WHERE prior_period_value IS NOT NULL
              AND ABS(semester_over_semester_abs_delta - (metric_value - prior_period_value)) > 0.0001
        """)
        college_mismatch = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_program_trend
            WHERE prior_period_value IS NOT NULL
              AND ABS(semester_over_semester_abs_delta - (metric_value - prior_period_value)) > 0.0001
        """)
        program_mismatch = cur.fetchone()[0]
    conn.close()
    assert college_mismatch == 0
    assert program_mismatch == 0


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_trend_classification_never_directional_below_min_periods():
    # Mirrors dbt/tests/assert_trend_classification_respects_min_periods.sql
    # as a pytest-level check too -- below trend_min_periods (dbt var,
    # default 3), every row must be 'insufficient_history', never a
    # directional label, regardless of what the raw slope says.
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_college_trend
            WHERE period_count < 3 AND trend_classification != 'insufficient_history'
        """)
        college_violations = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_program_trend
            WHERE period_count < 3 AND trend_classification != 'insufficient_history'
        """)
        program_violations = cur.fetchone()[0]
    conn.close()
    assert college_violations == 0
    assert program_violations == 0


@pytest.mark.skipif(not _marts_exist(), reason="Marts must be built first -- run `dbt run` before this test")
def test_mart_program_performance_dropout_rate_bounded():
    conn = get_role_connection("dbt_role", DBT_ROLE_PASSWORD, env=TEST_ENV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marts.mart_program_performance
            WHERE dropout_rate < 0 OR dropout_rate > 1
        """)
        out_of_bounds = cur.fetchone()[0]
    conn.close()
    assert out_of_bounds == 0