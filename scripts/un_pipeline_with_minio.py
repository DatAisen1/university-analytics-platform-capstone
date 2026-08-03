"""
scripts/run_pipeline_with_minio.py
Runs Bronze -> Silver -> Gold against REAL MinIO buckets instead of the
LocalFileStorage default every stage falls back to when no `storage`
argument is passed.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from pipelines.common.logging_config import PipelineStageLogger, get_logger
from pipelines.common.storage import load_minio_storage_from_env
from pipelines.gold.build_dimensions import build_all_dimensions
from pipelines.gold.build_facts import build_all_facts
from pipelines.gold.build_kpi import build_kpi
from pipelines.ingestion.ingest_to_bronze import ingest_all
from pipelines.silver.clean_entities import clean_all
from pipelines.silver.validate_and_dedupe import process_enrollment

_REPO_ROOT = Path(__file__).resolve().parents[1]
logger = get_logger(__name__)

def main() -> None:
    load_dotenv(_REPO_ROOT / ".env")
    bronze = load_minio_storage_from_env("MINIO_BRONZE_BUCKET")
    silver = load_minio_storage_from_env("MINIO_SILVER_BUCKET")
    gold = load_minio_storage_from_env("MINIO_GOLD_BUCKET")
    run_id = "manual-minio-run"

    with PipelineStageLogger(run_id, stage="ingestion") as sl:
        results = ingest_all(storage=bronze)
        sl.rows_processed = sum(r.get("rows", 0) for r in results if r.get("status") == "SUCCESS")
        logger.info("Bronze ingestion complete")

    with PipelineStageLogger(run_id, stage="silver") as sl:
        clean_results = clean_all(bronze_storage=bronze, silver_storage=silver)
        sl.rows_processed = sum(r.get("rows", 0) for r in clean_results if r["status"] == "SUCCESS")
        logger.info("Silver cleaning complete")

    with PipelineStageLogger(run_id, stage="validation", entity="enrollment") as sl:
        validation_summary = process_enrollment(silver_storage=silver)
        sl.rows_processed = validation_summary["rows_out"]
        sl.rows_rejected = validation_summary["total_quarantined"]
        logger.info("Silver validation complete")

    with PipelineStageLogger(run_id, stage="gold") as sl:
        dim_counts = build_all_dimensions(silver_storage=silver, gold_storage=gold)
        fact_counts = build_all_facts(silver_storage=silver, gold_storage=gold)
        kpi_summary = build_kpi(gold_storage=gold)
        sl.rows_processed = kpi_summary["rows"]
        logger.info("Gold build complete: dims=%s facts=%s", dim_counts, fact_counts)

    print("\nDone. Verify with: python3 scripts/verify_minio_data.py")

if __name__ == "__main__":
    main()  