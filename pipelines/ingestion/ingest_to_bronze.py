"""
pipelines/ingestion/ingest_to_bronze.py

Batch ingestion: picks up the data_generator's output (student master +
per-semester enrollment/graduation/dropout/shifter CSVs) plus the
project's reference data (colleges/programs), stamps audit metadata, and
lands everything in Bronze as Parquet -- untransformed, append-only, one
new batch-tagged file per run, never overwriting a prior ingestion.

Responsibilities this stage has (and does NOT have -- see
docs/05_Medallion_Architecture.md Section 2):
  - File-level checks only: does the source exist, is it non-empty, do
    the expected columns look present. NOT per-field schema validation
    (that's Day 9) and NOT business-rule correctness (that's Day 11).
  - Stamp metadata: _ingested_at, _source_file, _batch_id.
  - Idempotent: re-running this script skips any (entity, partition_key)
    that already has a SUCCESS row in pipeline_run_log, unless force=True.
    This is what makes "re-run ingestion, get the same end state" true --
    core to Day 8's validation checklist.

Run via: python -m pipelines.ingestion.ingest_to_bronze
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from pipelines.common.config import ReferenceData, load_default_reference_data
from pipelines.common.metadata import get_connection, has_successful_run, record_run
from pipelines.common.schemas import validate_bronze_dataframe
from pipelines.common.storage import LocalFileStorage, ObjectStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_GENERATOR_OUTPUT = _REPO_ROOT / "data_generator" / "output"
DEFAULT_BRONZE_STORAGE_PATH = _REPO_ROOT / "warehouse" / "bronze_store"  # local stand-in for the MinIO bronze bucket

STAGE = "bronze_ingestion"
SCHEMA_VALIDATION_STAGE = "bronze_schema_validation"

SEMESTER_SCOPED_ENTITIES = ["enrollment", "graduation", "dropout", "shifter"]
OBSERVED_SEMESTERS = [(year, sem) for year in (2021, 2022, 2023, 2024) for sem in (1, 2)]

REQUIRED_COLUMNS = {
    "college": ["college_id", "college_name"],
    "program": ["program_id", "program_name", "college_id", "program_level", "nominal_duration_years"],
    "student": ["student_id", "cohort_academic_year", "gender", "birth_year", "home_province",
                "admission_type", "entry_year_level", "entry_college_id", "entry_program_id"],
    "enrollment": ["student_id", "academic_year", "semester_name", "college_id", "program_id",
                   "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"],
    "graduation": ["student_id", "academic_year", "semester_name", "program_id", "college_id",
                   "years_to_complete"],
    "dropout": ["student_id", "academic_year", "semester_name", "program_id", "college_id",
                "dropout_reason", "semesters_completed_before_dropout"],
    "shifter": ["student_id", "academic_year", "semester_name", "from_program_id", "to_program_id"],
}


class IngestionError(Exception):
    """Raised for file-level ingestion problems: missing source, empty
    file, or missing expected columns. Distinct from ConfigError (which
    covers config authoring problems) -- this is about the DATA a batch
    was supposed to contain, not the pipeline's own configuration."""


def _validate_file_level(df: pd.DataFrame, entity: str, source_path: str) -> None:
    if df.empty:
        raise IngestionError(f"Source is empty: {source_path}")
    missing_cols = set(REQUIRED_COLUMNS[entity]) - set(df.columns)
    if missing_cols:
        raise IngestionError(
            f"{source_path} is missing expected column(s) for entity {entity!r}: {sorted(missing_cols)}"
        )


def _stamp_audit_columns(df: pd.DataFrame, batch_id: str, source_path: str) -> pd.DataFrame:
    df = df.copy()
    df["_ingested_at"] = datetime.now(timezone.utc)
    df["_source_file"] = source_path
    df["_batch_id"] = batch_id
    return df


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def _bronze_key(entity: str, partition_key: str, batch_id: str) -> str:
    return f"bronze/{entity}/{partition_key}/batch_id={batch_id}/data.parquet"


def _reference_data_to_dataframes(reference: ReferenceData) -> Dict[str, pd.DataFrame]:
    colleges_df = pd.DataFrame([c.model_dump() for c in reference.colleges])
    programs_df = pd.DataFrame([p.model_dump() for p in reference.programs])
    programs_df["program_level"] = programs_df["program_level"].astype(str)
    return {"college": colleges_df, "program": programs_df}


def _run_schema_validation(
    meta_conn, batch_id: str, entity: str, partition_key: str, df, source_path: str
) -> Dict[str, object]:
    """Post-write schema validation (Day 9): reports and logs violations
    but does NOT block or undo the Bronze write. Bronze's whole purpose is
    preserving exactly what was received, even if malformed -- rejecting
    it here would defeat that. Silver (Day 11) is where a quality gate
    that actually blocks promotion belongs.
    """
    import pandera.errors

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    try:
        validate_bronze_dataframe(df, entity)
        record_run(
            meta_conn, run_id, batch_id, SCHEMA_VALIDATION_STAGE, entity, partition_key,
            started_at, status="SUCCESS", rows_in=len(df), rows_out=len(df), source_path=source_path,
        )
        return {"entity": entity, "partition_key": partition_key, "status": "SCHEMA_VALID"}
    except pandera.errors.SchemaErrors as exc:
        failure_count = len(exc.failure_cases)
        summary = (
            f"{failure_count} schema violation(s): "
            + "; ".join(
                f"{row['column']}: {row['check']}"
                for _, row in exc.failure_cases.head(5).iterrows()
            )
        )
        record_run(
            meta_conn, run_id, batch_id, SCHEMA_VALIDATION_STAGE, entity, partition_key,
            started_at, status="FAILED", rows_in=len(df), source_path=source_path, error_message=summary,
        )
        return {"entity": entity, "partition_key": partition_key, "status": "SCHEMA_INVALID",
                "violation_count": failure_count, "summary": summary}


def ingest_one(
    storage: ObjectStorage,
    meta_conn,
    batch_id: str,
    entity: str,
    partition_key: str,
    source_path: str,
    load_fn: Callable[[], pd.DataFrame],
    force: bool = False,
) -> Dict[str, object]:
    """Ingest a single (entity, partition_key) unit: check idempotency,
    load, validate, stamp, write, log. Returns a small summary dict."""
    if not force and has_successful_run(meta_conn, STAGE, entity, partition_key):
        return {"entity": entity, "partition_key": partition_key, "status": "SKIPPED_ALREADY_INGESTED"}

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    try:
        df = load_fn()
        _validate_file_level(df, entity, source_path)
        rows_in = len(df)
        df = _stamp_audit_columns(df, batch_id, source_path)
        key = _bronze_key(entity, partition_key, batch_id)
        _write_parquet(storage, key, df)
        record_run(
            meta_conn, run_id, batch_id, STAGE, entity, partition_key,
            started_at, status="SUCCESS", rows_in=rows_in, rows_out=len(df), source_path=source_path,
        )
        schema_result = _run_schema_validation(meta_conn, batch_id, entity, partition_key, df, source_path)
        return {"entity": entity, "partition_key": partition_key, "status": "SUCCESS",
                "rows": rows_in, "key": key, "schema_validation": schema_result["status"]}
    except (IngestionError, FileNotFoundError) as exc:
        record_run(
            meta_conn, run_id, batch_id, STAGE, entity, partition_key,
            started_at, status="FAILED", source_path=source_path, error_message=str(exc),
        )
        return {"entity": entity, "partition_key": partition_key, "status": "FAILED", "error": str(exc)}


def ingest_all(
    storage: Optional[ObjectStorage] = None,
    meta_conn=None,
    data_generator_output: Path = DEFAULT_DATA_GENERATOR_OUTPUT,
    reference: Optional[ReferenceData] = None,
    force: bool = False,
) -> List[Dict[str, object]]:
    """Ingest reference data, student master, and every observed semester's
    enrollment/graduation/dropout/shifter files into Bronze. Returns a
    list of per-(entity, partition_key) result summaries.
    """
    storage = storage or LocalFileStorage(DEFAULT_BRONZE_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()
    reference = reference or load_default_reference_data()

    batch_id = str(uuid.uuid4())
    results: List[Dict[str, object]] = []

    ref_dataframes = _reference_data_to_dataframes(reference)
    for entity in ("college", "program"):
        source_path = f"configs/{entity}s.yaml"
        results.append(ingest_one(
            storage, meta_conn, batch_id, entity, "all", source_path,
            load_fn=lambda e=entity: ref_dataframes[e], force=force,
        ))

    student_master_path = data_generator_output / "student_master.csv"
    results.append(ingest_one(
        storage, meta_conn, batch_id, "student", "all", str(student_master_path),
        load_fn=lambda: pd.read_csv(student_master_path), force=force,
    ))

    for year, sem in OBSERVED_SEMESTERS:
        partition_key = f"academic_year={year}/semester={sem}"
        for entity in SEMESTER_SCOPED_ENTITIES:
            source_path = data_generator_output / str(year) / str(sem) / f"{entity}.csv"
            if not source_path.exists():
                # Not every partition has every entity (e.g. an early
                # semester may have zero graduations) -- that's a valid
                # real-world state, not an ingestion failure.
                results.append({"entity": entity, "partition_key": partition_key, "status": "NO_SOURCE_FILE"})
                continue
            results.append(ingest_one(
                storage, meta_conn, batch_id, entity, partition_key, str(source_path),
                load_fn=lambda p=source_path: pd.read_csv(p), force=force,
            ))

    if owns_conn:
        meta_conn.close()

    return results


if __name__ == "__main__":
    summary = ingest_all()
    counts: Dict[str, int] = {}
    for r in summary:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Bronze ingestion complete:")
    for status, count in counts.items():
        print(f"  {status}: {count}")
