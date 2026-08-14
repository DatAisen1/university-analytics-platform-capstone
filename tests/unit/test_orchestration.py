"""
tests/unit/test_orchestration.py

Tests for orchestration/ (Days 18-19). Split into two tiers:

  - Structural tests (always run, no external services needed): the
    Definitions object loads, and the asset dependency graph matches
    docs/02_System_Architecture.md's orchestration diagram exactly.
  - Full materialization test (skipped if Postgres/dbt unreachable): an
    actual subprocess-level `dagster asset materialize --select <all ten
    assets, named explicitly>` run, proving the entire pipeline executes
    end-to-end THROUGH Dagster's own execution engine. The ten assets are
    named explicitly rather than selected via `--select "*"` -- see the
    comment at the subprocess.run call for why the wildcard form is
    avoided.
"""

import os
import shutil
import subprocess
import sys
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
    """The orchestrator should expose an explicit stage-by-stage lineage.
    The ML branch must only run once the warehouse-ready gold data is
    available and validated."""
    from orchestration.definitions import defs

    resolved = defs.resolve_asset_graph()
    order = [key.to_user_string() for key in resolved.toposorted_asset_keys]

    prerequisite_order = [
        "bronze",
        "silver",
        "validation",
        "gold",
        "warehouse",
        "dbt",
        "features",
        "training",
        "evaluation",
        "forecast",
    ]
    assert order[:10] == prerequisite_order


def test_each_asset_depends_only_on_its_documented_predecessor():
    """Each explicit stage should only depend on the immediately prior stage."""
    from orchestration.definitions import defs

    resolved = defs.resolve_asset_graph()

    def upstream_of(name: str) -> set:
        from dagster import AssetKey
        return {k.to_user_string() for k in resolved.get(AssetKey(name)).parent_keys}

    assert upstream_of("bronze") == set()
    assert upstream_of("silver") == {"bronze"}
    assert upstream_of("validation") == {"silver"}
    assert upstream_of("gold") == {"validation"}
    assert upstream_of("warehouse") == {"gold"}
    assert upstream_of("dbt") == {"warehouse"}
    assert upstream_of("features") == {"dbt"}
    assert upstream_of("training") == {"features"}
    assert upstream_of("evaluation") == {"training"}
    assert upstream_of("forecast") == {"evaluation"}


def test_schedule_cadence_is_twice_yearly_not_a_shorter_cycle():
    from orchestration.definitions import semester_schedule

    cron_parts = semester_schedule.cron_schedule.split()
    month_field = cron_parts[3]
    months = month_field.split(",")
    assert len(months) == 2


def test_full_pipeline_job_selects_all_ten_assets():
    from orchestration.definitions import all_assets, full_pipeline_job
    assert len(all_assets) == 10
    assert full_pipeline_job.name == "full_pipeline_job"


def test_pipeline_run_tracking_helper_writes_expected_fields():
    from pipelines.common.metadata import get_connection, record_pipeline_run
    from pathlib import Path

    db_path = Path("warehouse/test_pipeline_runs.duckdb")
    if db_path.exists():
        db_path.unlink()

    conn = get_connection(db_path)
    record_pipeline_run(
        conn,
        run_id="run-123",
        stage="training",
        status="SUCCESS",
        started_at="2026-08-03T00:00:00+00:00",
        completed_at="2026-08-03T00:01:00+00:00",
        records_processed=42,
        error="",
    )

    row = conn.execute(
        "SELECT run_id, stage, status, records_processed, error FROM dagster_pipeline_runs WHERE run_id = ?",
        ["run-123"],
    ).fetchone()

    assert row == ("run-123", "training", "SUCCESS", 42, "")


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


def _dbt_available() -> bool:
    return shutil.which("dbt") is not None


def _generated_dataset_available() -> bool:
    """The bronze asset's semester-scoped entities (student, enrollment,
    dropout, graduation, shifter) source from data_generator/output/,
    which `python -m data_generator.generators.generate_all` must be run
    to produce -- it is not checked into the repo and nothing else in
    this test module builds it. Without it, bronze silently no-ops on
    those entities (NO_SOURCE_FILE, not a failure), silver never writes
    their Parquet files, and validation dies several layers downstream
    with a FileNotFoundError that looks like a CLI/config bug but isn't
    one. Skip with a clear reason instead of failing confusingly."""
    return (_REPO_ROOT / "data_generator" / "output" / "student_master.csv").exists()


@pytest.mark.skipif(
    not (_postgres_available() and _dbt_available() and _generated_dataset_available()),
    reason=(
        "Requires a reachable Postgres instance, the dbt CLI, and a generated "
        "dataset -- run `python -m data_generator.generators.generate_all` first"
    ),
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

    # `--select "*"` is avoided deliberately. On at least one Windows/
    # dagster-1.13.14 combination this wildcard gets expanded into the
    # repo's own directory listing before Click's option parser ever
    # sees it (reproduced identically via a compiled `dagster.exe`
    # shim, `python -m dagster`, and pytest's own subprocess.run -- so
    # it is not a shell-quoting issue, and not fixed by changing how
    # the caller is invoked). Rather than depend on wildcard-selection
    # behavior that isn't reliable across platforms/versions, select
    # the ten pipeline assets explicitly -- the same fixed list
    # test_full_pipeline_job_selects_all_ten_assets already asserts
    # against, so this stays in sync with that test by construction.
    # This is also arguably the more correct test regardless of the
    # Windows quirk: it proves this exact graph materializes, not
    # "whatever `*` happens to resolve to."
    asset_selection = (
        "bronze,silver,validation,gold,warehouse,dbt,features,training,evaluation,forecast"
    )
    result = subprocess.run(
        [sys.executable, "-m", "dagster", "asset", "materialize", "--select", asset_selection, "-f", "orchestration/definitions.py"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUN_SUCCESS" in result.stdout or "RUN_SUCCESS" in result.stderr

    # P0.45/P0.44 fix: this list previously named assets that never
    # existed in orchestration/assets.py under any version of this
    # codebase (bronze_layer, gold_dimensions, gold_facts, gold_kpi,
    # dbt_staging_and_marts, ...) -- a stale/aspirational asset-naming
    # scheme that was never reconciled with the actual 10-asset graph.
    # Corrected to the real asset names (see all_assets in
    # orchestration/assets.py). The exact "Materialized value <name>"
    # substring format could not be verified against a live `dagster`
    # CLI in this environment (no dagster/Postgres/dbt available) --
    # confirm this assertion format the first time this test actually
    # runs (Postgres + dbt CLI both present).
    combined_output = result.stdout + result.stderr
    for asset_name in [
        "bronze", "silver", "validation", "gold", "warehouse",
        "dbt", "features", "training", "evaluation", "forecast",
    ]:
        assert f"Materialized value {asset_name}" in combined_output, f"{asset_name} did not materialize"