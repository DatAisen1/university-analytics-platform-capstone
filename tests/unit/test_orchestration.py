"""
tests/unit/test_orchestration.py

Tests for orchestration/ (Days 18-19). Split into two tiers:

  - Structural tests (always run, no external services needed): the
    Definitions object loads, and the asset dependency graph matches
    docs/02_System_Architecture.md's orchestration diagram exactly.
  - Full materialization test (skipped if Postgres/dbt unreachable): an
    actual subprocess-level `dagster asset materialize --select "*"` run,
    proving the entire pipeline executes end-to-end THROUGH Dagster's own
    execution engine.
"""

import os
import shutil
import subprocess
from pathlib import Path

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


def test_definitions_loads_without_error():
    from orchestration.definitions import defs
    assert defs is not None


def test_asset_dependency_order_matches_architecture_diagram():
    """docs/02_System_Architecture.md's orchestration diagram, in order:
    ingest_to_bronze -> bronze_to_silver -> silver_to_gold -> dbt run+test
    (+ ml_forecast_features, added Day 19, a sibling of the dbt asset).
    ml_forecast_features and dbt_staging_and_marts have no ordering
    constraint between each other (both only depend on gold_in_postgres),
    so either could legally come first in a toposort."""
    from orchestration.definitions import defs

    resolved = defs.resolve_asset_graph()
    order = [key.to_user_string() for key in resolved.toposorted_asset_keys]

    prerequisite_order = [
        "bronze_layer", "silver_cleaned", "silver_validated",
        "gold_dimensions", "gold_facts", "gold_kpi", "gold_in_postgres",
    ]
    assert order[:7] == prerequisite_order
    assert set(order[7:]) == {"ml_forecast_features", "dbt_staging_and_marts"}


def test_each_asset_depends_only_on_its_documented_predecessor():
    """A stronger check than toposort order alone: toposort only proves
    A VALID ordering exists, not that the edges themselves are what's
    documented."""
    from orchestration.definitions import defs

    resolved = defs.resolve_asset_graph()

    def upstream_of(name: str) -> set:
        from dagster import AssetKey
        return {k.to_user_string() for k in resolved.get(AssetKey(name)).parent_keys}

    assert upstream_of("bronze_layer") == set()
    assert upstream_of("silver_cleaned") == {"bronze_layer"}
    assert upstream_of("silver_validated") == {"silver_cleaned"}
    assert upstream_of("gold_dimensions") == {"silver_validated"}
    assert upstream_of("gold_facts") == {"gold_dimensions"}
    assert upstream_of("gold_kpi") == {"gold_facts"}
    assert upstream_of("gold_in_postgres") == {"gold_kpi"}
    assert upstream_of("ml_forecast_features") == {"gold_in_postgres"}
    assert upstream_of("dbt_staging_and_marts") == {"gold_in_postgres"}


def test_schedule_cadence_is_twice_yearly_not_a_shorter_cycle():
    from orchestration.definitions import semester_schedule

    cron_parts = semester_schedule.cron_schedule.split()
    month_field = cron_parts[3]
    months = month_field.split(",")
    assert len(months) == 2


def test_full_pipeline_job_selects_all_nine_assets():
    from orchestration.definitions import all_assets, full_pipeline_job
    assert len(all_assets) == 9
    assert full_pipeline_job.name == "full_pipeline_job"


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


def _dbt_available() -> bool:
    return shutil.which("dbt") is not None


@pytest.mark.skipif(
    not (_postgres_available() and _dbt_available()),
    reason="Requires both a reachable Postgres instance and the dbt CLI",
)
def test_full_pipeline_materializes_successfully_via_dagster():
    """Day 18's core validation checklist item: trigger a real run and
    verify every asset materializes -- through Dagster's own execution
    engine, not by calling the underlying pipeline functions directly."""
    env = dict(os.environ)
    env.update({
        "PIPELINE_WRITER_PASSWORD": os.environ.get("TEST_PIPELINE_WRITER_PASSWORD", "pw_pipeline123"),
        "DBT_ROLE_PASSWORD": os.environ.get("TEST_DBT_ROLE_PASSWORD", "pw_dbt123"),
        "POSTGRES_HOST": TEST_ENV["POSTGRES_HOST"],
        "POSTGRES_PORT": TEST_ENV["POSTGRES_PORT"],
        "POSTGRES_DB": TEST_ENV["POSTGRES_DB"],
    })

    result = subprocess.run(
        ["dagster", "asset", "materialize", "--select", "*", "-f", "orchestration/definitions.py"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUN_SUCCESS" in result.stdout or "RUN_SUCCESS" in result.stderr

    combined_output = result.stdout + result.stderr
    for asset_name in [
        "bronze_layer", "silver_cleaned", "silver_validated", "gold_dimensions",
        "gold_facts", "gold_kpi", "gold_in_postgres", "ml_forecast_features", "dbt_staging_and_marts",
    ]:
        assert f"Materialized value {asset_name}" in combined_output, f"{asset_name} did not materialize"
