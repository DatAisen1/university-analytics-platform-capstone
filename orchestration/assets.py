"""
orchestration/assets.py

Dagster asset-based orchestration for the full university analytics
pipeline. The lineage is expressed explicitly as a single staged graph:
ingestion -> bronze -> silver -> validation -> gold -> warehouse ->
features -> training -> evaluation -> forecast.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dagster import AssetExecutionContext, MetadataValue, asset
from pipelines.common.errors import PipelineError, classify_exception
from pipelines.common.logging_config import PipelineStageLogger
from pipelines.common.settings import get_postgres_settings
from models.forecasting.deploy_forecast import deploy_forecasts
from models.forecasting.train_prophet import evaluate_all_series, train_final_models, write_evaluation_report
from pipelines.common.metadata import get_connection, record_pipeline_run
from pipelines.gold.build_dimensions import build_all_dimensions
from pipelines.gold.build_facts import build_all_facts
from pipelines.gold.build_kpi import build_kpi
from pipelines.gold.build_ml_features import build_and_store_ml_features
from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine, load_gold_to_postgres
from pipelines.ingestion.ingest_to_bronze import ingest_all
from pipelines.silver.clean_entities import clean_all
from pipelines.silver.load_silver_to_postgres import load_silver_to_postgres
from pipelines.silver.validate_and_dedupe import process_enrollment

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _track_asset_run(context: AssetExecutionContext, stage: str, handler):
    started_at = datetime.now(timezone.utc)
    conn = get_connection()
    record_pipeline_run(
        conn,
        run_id=context.run_id,
        stage=stage,
        status="RUNNING",
        started_at=started_at,
        completed_at=None,
        records_processed=0,
        error="",
    )
    # Task 63: one STARTED + one terminal SUCCESS/FAILED JSON log line
    # per stage, correlated by run_id, in addition to the DuckDB row
    # below -- see pipelines/common/logging_config.py's module docstring
    # for why both exist.
    stage_log = PipelineStageLogger(run_id=context.run_id, stage=stage)
    try:
        with stage_log:
            result = handler()
            stage_log.rows_processed = (
                result["records_processed"] if isinstance(result, dict) else 0
            )
        completed_at = datetime.now(timezone.utc)
        record_pipeline_run(
            conn,
            run_id=context.run_id,
            stage=stage,
            status="SUCCESS",
            started_at=started_at,
            completed_at=completed_at,
            records_processed=stage_log.rows_processed,
            error="",
        )
        return result
    except Exception as exc:  # pragma: no cover - error path exercised via Dagster runtime
        completed_at = datetime.now(timezone.utc)
        # Task 46/47: classify_exception is a no-op passthrough if the
        # handler already raised a PipelineError subclass (the common
        # case once modules below are updated); it only synthesizes a
        # category for a third-party exception that slipped through.
        pipeline_error = classify_exception(exc, stage=stage)
        record_pipeline_run(
            conn,
            run_id=context.run_id,
            stage=stage,
            status="FAILED",
            started_at=started_at,
            completed_at=completed_at,
            records_processed=0,
            error=pipeline_error.message,
            error_category=pipeline_error.category.value,
            rows_affected=pipeline_error.rows_affected,
        )
        # Task 47: the traceable, structured report -- Stage / Error /
        # Rows affected -- goes to Dagster's own run log, not just a
        # bare "Pipeline failed".
        context.log.error(pipeline_error.to_report())
        raise pipeline_error from exc


@asset(group_name="ingestion")
def ingestion(context: AssetExecutionContext) -> dict:
    """Materializes the inbound semester extract into bronze data."""

    def _run() -> dict:
        results = ingest_all()
        counts = {}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"records_processed": sum(item.get("rows", 0) for item in results if item.get("status") == "SUCCESS"), "result_counts": counts}

    result = _track_asset_run(context, "ingestion", _run)
    context.add_output_metadata({"result_counts": MetadataValue.json(result["result_counts"]), "records_processed": result["records_processed"]})
    return result


@asset(group_name="bronze", deps=[ingestion])
def bronze(context: AssetExecutionContext) -> dict:
    """Persists the bronze layer and ensures the ingestion output is available for downstream cleaning."""

    def _run() -> dict:
        results = ingest_all()
        counts = {}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"records_processed": sum(item.get("rows", 0) for item in results if item.get("status") == "SUCCESS"), "result_counts": counts}

    result = _track_asset_run(context, "bronze", _run)
    context.add_output_metadata({"result_counts": MetadataValue.json(result["result_counts"]), "records_processed": result["records_processed"]})
    return result


@asset(group_name="silver", deps=[bronze])
def silver(context: AssetExecutionContext) -> dict:
    """Runs bronze-to-silver cleaning so downstream validation uses cleansed data."""

    def _run() -> dict:
        results = clean_all()
        rows_by_entity = {item["entity"]: item.get("rows", 0) for item in results if item["status"] == "SUCCESS"}
        return {"records_processed": sum(rows_by_entity.values()), "rows_by_entity": rows_by_entity}

    result = _track_asset_run(context, "silver", _run)
    context.add_output_metadata({"rows_by_entity": MetadataValue.json(result["rows_by_entity"]), "records_processed": result["records_processed"]})
    return result


@asset(group_name="validation", deps=[silver])
def validation(context: AssetExecutionContext) -> dict:
    """Applies validation and deduplication before gold modeling begins."""

    def _run() -> dict:
        summary = process_enrollment()
        return {
            "records_processed": summary["rows_out"],
            "duplicates_dropped": summary["duplicates_dropped"],
            "quarantine_rate": summary["quarantine_rate"],
        }

    result = _track_asset_run(context, "validation", _run)
    context.add_output_metadata({
        "rows_out": result["records_processed"],
        "duplicates_dropped": result["duplicates_dropped"],
        "quarantine_rate": MetadataValue.text(f"{result['quarantine_rate']:.2%}"),
    })
    return result


@asset(group_name="gold", deps=[validation])
def gold(context: AssetExecutionContext) -> dict:
    """Builds the gold dimensions, facts, and KPI layer from validated silver data."""

    def _run() -> dict:
        dimension_counts = build_all_dimensions()
        fact_counts = build_all_facts()
        kpi_summary = build_kpi()
        return {
            "records_processed": kpi_summary["rows"],
            "dimension_counts": dimension_counts,
            "fact_counts": fact_counts,
            "kpi_rows": kpi_summary["rows"],
        }

    result = _track_asset_run(context, "gold", _run)
    context.add_output_metadata({
        "dimension_counts": MetadataValue.json(result["dimension_counts"]),
        "fact_counts": MetadataValue.json(result["fact_counts"]),
        "kpi_rows": result["kpi_rows"],
    })
    return result


@asset(group_name="warehouse", deps=[gold])
def warehouse(context: AssetExecutionContext) -> dict:
    """Loads the gold layer into the warehouse so downstream ML assets consume validated data."""

    def _run() -> dict:
        password = get_postgres_settings().require_pipeline_writer_password()
        engine = build_pipeline_writer_engine(password)
        counts = load_gold_to_postgres(engine)
        return {"records_processed": sum(counts.values()), "row_counts": counts}

    result = _track_asset_run(context, "warehouse", _run)
    context.add_output_metadata({"row_counts": MetadataValue.json(result["row_counts"]), "records_processed": result["records_processed"]})
    return result


@asset(group_name="features", deps=[warehouse])
def features(context: AssetExecutionContext) -> dict:
    """Builds leakage-safe forecasting features from the warehouse-backed gold data."""

    def _run() -> dict:
        password = get_postgres_settings().require_pipeline_writer_password()
        engine = build_pipeline_writer_engine(password)
        row_count = build_and_store_ml_features(engine)
        return {"records_processed": row_count, "row_count": row_count}

    result = _track_asset_run(context, "features", _run)
    context.add_output_metadata({"rows": result["row_count"]})
    return result


@asset(group_name="training", deps=[features])
def training(context: AssetExecutionContext) -> dict:
    """Trains forecasting models for each series after the feature layer is ready."""

    def _run() -> dict:
        password = get_postgres_settings().require_pipeline_writer_password()
        engine = build_pipeline_writer_engine(password)
        saved_paths = train_final_models(engine)
        return {"records_processed": len(saved_paths), "saved_paths": saved_paths}

    result = _track_asset_run(context, "training", _run)
    context.add_output_metadata({"saved_paths": MetadataValue.json(result["saved_paths"]), "records_processed": result["records_processed"]})
    return result


@asset(group_name="evaluation", deps=[training])
def evaluation(context: AssetExecutionContext) -> dict:
    """Runs walk-forward evaluation and writes the evaluation report for the trained models."""

    def _run() -> dict:
        password = get_postgres_settings().require_pipeline_writer_password()
        engine = build_pipeline_writer_engine(password)
        report = evaluate_all_series(engine)
        csv_path, md_path = write_evaluation_report(report)
        return {"records_processed": len(report), "report_path": str(csv_path), "summary_path": str(md_path)}

    result = _track_asset_run(context, "evaluation", _run)
    context.add_output_metadata({"records_processed": result["records_processed"], "report_path": result["report_path"], "summary_path": result["summary_path"]})
    return result


@asset(group_name="forecast", deps=[evaluation])
def forecast(context: AssetExecutionContext) -> dict:
    """Deploys the promoted forecasting model to produce the next period forecast."""

    def _run() -> dict:
        password = get_postgres_settings().require_pipeline_writer_password()
        engine = build_pipeline_writer_engine(password)
        deployments = deploy_forecasts(engine)
        return {"records_processed": len(deployments), "deployments": [deployment.__dict__ for deployment in deployments]}

    result = _track_asset_run(context, "forecast", _run)
    context.add_output_metadata({"records_processed": result["records_processed"], "deployments": MetadataValue.json(result["deployments"])})
    return result


all_assets = [
    ingestion,
    bronze,
    silver,
    validation,
    gold,
    warehouse,
    features,
    training,
    evaluation,
    forecast,
]