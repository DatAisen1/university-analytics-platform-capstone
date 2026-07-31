"""
orchestration/assets.py

Dagster asset-based orchestration for the full Bronze -> Silver -> Gold ->
dbt pipeline, matching docs/02_System_Architecture.md's orchestration
diagram exactly: each Dagster asset corresponds to one node in that
diagram, and the dependency edges Dagster infers from function
parameters ARE the lineage graph shown in the Dagster UI.

Deliberately NOT included as a Dagster asset: the data_generator/ scripts
(Days 4-6). Those stand in for a real Student Information System producing
semester extracts -- Dagster orchestrates what happens to data once it
ARRIVES (matching the architecture diagram's "Semester Data Arrives ->
Orchestrator: Dagster -> ingest_to_bronze -> ..."), not the external
system that produces it.

Why Dagster's asset model, not Airflow's DAG-of-tasks model (see
docs/07_Technology_Stack.md's comparison): "software-defined assets" map
almost one-to-one onto Bronze/Silver/Gold, which ARE assets (persistent
data, not just steps in a workflow) -- the lineage graph this file
produces IS the medallion architecture diagram, not an abstraction over it.

On the dbt integration: a fuller `dagster-dbt` integration (one Dagster
asset PER dbt model, auto-generated from the manifest via @dbt_assets)
is the more idiomatic production pattern. This project uses a single
subprocess-wrapped asset instead -- a reasonable, honest capstone-scope
choice that still demonstrates the orchestration principle (dbt as one
node with real upstream/downstream edges), deferred to
docs/14_Future_Improvements.md as the natural next step rather than
built now.

Note: `from __future__ import annotations` is intentionally NOT used in
this file. Dagster's @asset decorator inspects the `context` parameter's
real type at runtime to decide what to inject; postponed evaluation
(PEP 563) would turn that annotation into the string
"AssetExecutionContext" instead of the actual class, and Dagster's
decorator does not re-resolve it -- it fails with a confusing error
("must be annotated with AssetExecutionContext...") even though the
annotation visually says exactly that. Found by hitting the error
directly, not by reading Dagster's source in advance.
"""

import os
import subprocess
from pathlib import Path

from dagster import AssetExecutionContext, MetadataValue, asset

from pipelines.gold.build_dimensions import build_all_dimensions
from pipelines.gold.build_facts import build_all_facts
from pipelines.gold.build_kpi import build_kpi
from pipelines.gold.build_ml_features import build_and_store_ml_features
from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine, load_gold_to_postgres
from pipelines.ingestion.ingest_to_bronze import ingest_all
from pipelines.silver.clean_entities import clean_all
from pipelines.silver.validate_and_dedupe import process_enrollment

_REPO_ROOT = Path(__file__).resolve().parents[1]


@asset(group_name="bronze")
def bronze_layer(context: AssetExecutionContext) -> None:
    """Batch ingestion: data_generator's output -> Bronze Parquet, stamped
    with audit metadata (Day 8), schema-validated as a post-write check
    (Day 9)."""
    results = ingest_all()
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    context.add_output_metadata({"result_counts": MetadataValue.json(counts)})


@asset(group_name="silver", deps=[bronze_layer])
def silver_cleaned(context: AssetExecutionContext) -> None:
    """Bronze -> Silver cleaning: text hygiene + enrollment_status
    normalization via DuckDB SQL (Day 10)."""
    results = clean_all()
    rows_by_entity = {r["entity"]: r.get("rows", 0) for r in results if r["status"] == "SUCCESS"}
    context.add_output_metadata({"rows_by_entity": MetadataValue.json(rows_by_entity)})


@asset(group_name="silver", deps=[silver_cleaned])
def silver_validated(context: AssetExecutionContext) -> None:
    """Dedup (last-write-wins) + business-rule quarantine on enrollment
    (Day 11)."""
    summary = process_enrollment()
    context.add_output_metadata({
        "rows_out": summary["rows_out"],
        "duplicates_dropped": summary["duplicates_dropped"],
        "quarantine_rate": MetadataValue.text(f"{summary['quarantine_rate']:.2%}"),
    })


@asset(group_name="gold", deps=[silver_validated])
def gold_dimensions(context: AssetExecutionContext) -> None:
    """dim_college, dim_program, dim_academic_year, dim_semester,
    dim_calendar, and dim_student (real SCD2 history) (Day 12)."""
    counts = build_all_dimensions()
    context.add_output_metadata({"row_counts": MetadataValue.json(counts)})


@asset(group_name="gold", deps=[gold_dimensions])
def gold_facts(context: AssetExecutionContext) -> None:
    """fact_enrollment, fact_graduation, fact_dropout, fact_shifter,
    fact_retention -- resolved via AS-OF join against dim_student's SCD2
    history (Day 13)."""
    counts = build_all_facts()
    context.add_output_metadata({"row_counts": MetadataValue.json(counts)})


@asset(group_name="gold", deps=[gold_facts])
def gold_kpi(context: AssetExecutionContext) -> None:
    """fact_institution_kpi: the weighted Success Rate composite
    (Day 14)."""
    summary = build_kpi()
    context.add_output_metadata({"rows": summary["rows"]})


@asset(group_name="warehouse", deps=[gold_kpi])
def gold_in_postgres(context: AssetExecutionContext) -> None:
    """Materializes Gold Parquet into real Postgres gold.* tables via
    pipeline_writer (Day 15's RBAC role for exactly this), using
    TRUNCATE + append so dbt's dependent staging views survive a reload
    (Day 16's fix)."""
    password = os.environ["PIPELINE_WRITER_PASSWORD"]
    engine = build_pipeline_writer_engine(password)
    counts = load_gold_to_postgres(engine)
    context.add_output_metadata({"row_counts": MetadataValue.json(counts)})


@asset(group_name="analytics", deps=[gold_in_postgres])
def ml_forecast_features(context: AssetExecutionContext) -> None:
    """gold.ml_forecast_features: leakage-free lag/rolling/trend/
    seasonality features per college per semester, built via SQL window
    functions (Day 19). A sibling of dbt_staging_and_marts, not a
    dependency of it -- dbt's marts don't consume ML features, and the ML
    features don't need dbt's marts, so there's no real ordering
    constraint between them, only a shared upstream (gold_in_postgres)."""
    password = os.environ["PIPELINE_WRITER_PASSWORD"]
    engine = build_pipeline_writer_engine(password)
    row_count = build_and_store_ml_features(engine)
    context.add_output_metadata({"rows": row_count})


@asset(group_name="analytics", deps=[gold_in_postgres])
def dbt_staging_and_marts(context: AssetExecutionContext) -> None:
    """Runs the full dbt project (12 staging views + 5 marts) and its
    test suite against the live warehouse (Days 16-17)."""
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(_REPO_ROOT / "dbt")

    run_result = subprocess.run(
        ["dbt", "run", "--project-dir", str(_REPO_ROOT / "dbt")],
        capture_output=True, text=True, env=env,
    )
    if run_result.returncode != 0:
        raise RuntimeError(f"dbt run failed:\n{run_result.stdout}\n{run_result.stderr}")

    test_result = subprocess.run(
        ["dbt", "test", "--project-dir", str(_REPO_ROOT / "dbt")],
        capture_output=True, text=True, env=env,
    )
    if test_result.returncode != 0:
        raise RuntimeError(f"dbt test failed:\n{test_result.stdout}\n{test_result.stderr}")

    context.add_output_metadata({
        "dbt_run_output": MetadataValue.text(run_result.stdout[-2000:]),
        "dbt_test_output": MetadataValue.text(test_result.stdout[-2000:]),
    })
