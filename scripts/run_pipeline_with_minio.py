"""
scripts/run_pipeline_with_minio.py

Runs Bronze -> Silver -> Gold against REAL MinIO buckets instead of the
LocalFileStorage default that pipelines/ingestion/ingest_to_bronze.py,
pipelines/silver/*.py, and pipelines/gold/*.py all fall back to when no
`storage` argument is passed.

Why this exists as a separate script instead of changing the defaults:
LocalFileStorage is the correct default for a Docker-less dev/CI
environment (see storage.py's module docstring) -- changing that default
would break anyone running tests or working without Docker. This script
is the explicit opt-in for "I have `make up` running and I want bytes in
MinIO," built entirely from functions that already exist
(load_minio_storage_from_env + every stage's own storage parameters) --
no pipeline logic is duplicated here.

Usage (after `make up` / `make clean-start`, with .env populated):
    python -m scripts.run_pipeline_with_minio
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

    with PipelineStageLogger(run_id, stage="ingestion") as stage_log:
        # force=True: has_successful_run() checks (stage, entity,
        # partition_key) in warehouse/meta.duckdb, which is shared across
        # EVERY storage backend. If you've ever run this pipeline against
        # LocalFileStorage before (e.g. the earlier "test it right now"
        # walkthrough), those SUCCESS rows already exist -- and without
        # force=True, ingest_one() would see "already ingested" and skip
        # every entity, writing nothing to MinIO at all while still
        # reporting a clean, suspiciously-fast SUCCESS.
        results = ingest_all(storage=bronze, force=True)
        counts = {}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        stage_log.rows_processed = sum(
            r.get("rows", 0) for r in results if r.get("status") == "SUCCESS"
        )
        logger.info("Bronze ingestion complete: %s", counts, extra={"pipeline_extra": counts})

    with PipelineStageLogger(run_id, stage="silver") as stage_log:
        clean_results = clean_all(bronze_storage=bronze, silver_storage=silver)
        stage_log.rows_processed = sum(
            r.get("rows", 0) for r in clean_results if r["status"] == "SUCCESS"
        )
        logger.info("Silver cleaning complete: %s", clean_results, extra={"pipeline_extra": {"results": clean_results}})

    with PipelineStageLogger(run_id, stage="validation", entity="enrollment") as stage_log:
        validation_summary = process_enrollment(silver_storage=silver)
        stage_log.rows_processed = validation_summary["rows_out"]
        stage_log.rows_rejected = validation_summary["total_quarantined"]
        logger.info("Silver validation complete: %s", validation_summary, extra={"pipeline_extra": validation_summary})

    with PipelineStageLogger(run_id, stage="gold") as stage_log:
        dim_counts = build_all_dimensions(silver_storage=silver, gold_storage=gold)
        fact_counts = build_all_facts(silver_storage=silver, gold_storage=gold)
        kpi_summary = build_kpi(gold_storage=gold)
        stage_log.rows_processed = kpi_summary["rows"]
        logger.info(
            "Gold build complete: dims=%s facts=%s kpi_rows=%s",
            dim_counts, fact_counts, kpi_summary["rows"],
            extra={"pipeline_extra": {"dimension_counts": dim_counts, "fact_counts": fact_counts}},
        )

    print("\nDone. Verify with: python3 scripts/verify_minio_data.py")


if __name__ == "__main__":
    main()