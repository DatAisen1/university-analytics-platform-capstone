"""
pipelines/ingestion/ingest_to_bronze.py

Batch ingestion: picks up the data_generator's output (student master +
per-semester enrollment/graduation/dropout/shifter CSVs) plus the
project's reference data (colleges/programs), stamps audit metadata, and
lands everything in Bronze as Parquet -- untransformed, append-only, one
new batch-tagged file per run, never overwriting a prior ingestion.

Every source unit goes through five explicit, named stages, in order,
inside `ingest_one`:

    1. READ       -- load_fn() pulls the raw rows (CSV or reference config).
    2. INSPECT     -- _inspect_schema(): a non-raising snapshot of what
                       columns/dtypes/row-count we actually received, for
                       logging/observability. Never a gate.
    3. NORMALIZE   -- _normalize_column_names(): header hygiene ONLY
                       (trim whitespace, lowercase). Never touches cell
                       values and never renames a business column to a
                       different name -- that would be a data-modeling
                       decision, which belongs in Silver
                       (pipelines/silver/cleaning_rules.py), not here.
    4. VALIDATE    -- _validate_file_level(): the actual gate. Raises
                       IngestionError on an empty file or missing
                       required column, which SKIPS the Bronze write
                       below -- unvalidated data never reaches storage.
    5. WRITE       -- only validated, normalized-header data is stamped
                       with audit columns and written to Bronze.

Responsibilities this stage has (and does NOT have -- see
docs/05_Medallion_Architecture.md Section 2):
  - File-level checks only: does the source exist, is it non-empty, do
    the expected columns look present. NOT per-field schema validation
    (that's pipelines/common/schemas.py, run as a post-write report --
    see _run_schema_validation below) and NOT business-rule correctness
    (that's Silver's job).
  - Stamp metadata: _ingested_at, _source_file, _batch_id.
  - Idempotent: re-running this script skips any (entity, partition_key)
    that already has a SUCCESS row in pipeline_run_log, unless force=True.
    This is what makes "re-run ingestion, get the same end state" true.

Run via: python -m pipelines.ingestion.ingest_to_bronze
To independently verify what actually landed in Bronze afterward, run:
    python -m pipelines.ingestion.audit_bronze
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
from pipelines.common.academic_periods import OBSERVED_ACADEMIC_YEARS, academic_year_label, semester_label_from_number
from pipelines.common.errors import InvalidSchemaError
from pipelines.common.config import ReferenceData, load_default_reference_data
from pipelines.common.metadata import get_connection, has_successful_run, record_run, record_success_once
from pipelines.common.schemas import validate_bronze_dataframe
from pipelines.common.storage import ObjectStorage, load_storage_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_GENERATOR_OUTPUT = _REPO_ROOT / "data_generator" / "output"
DEFAULT_BRONZE_STORAGE_PATH = _REPO_ROOT / "warehouse" / "bronze_store"  # local stand-in for the MinIO bronze bucket

STAGE = "bronze_ingestion"
SCHEMA_VALIDATION_STAGE = "bronze_schema_validation"

SEMESTER_SCOPED_ENTITIES = ["enrollment", "graduation", "dropout", "shifter"]

# P0 (Dataset Extension) fix: previously hardcoded as
# `(2021, 2022, 2023, 2024)` -- a leftover from the pre-P0.4, incorrect
# 8-semester model (see docs/10_Forecasting.md §1) that was never updated
# when the calendar was corrected to the 6-semester (2021-2023) window.
# It didn't crash (the loop below already handles a missing partition as
# NO_SOURCE_FILE, not an error), but it meant Bronze ingestion silently
# looked for 2024 data that the documented model said didn't exist --
# exactly the kind of stale, independently-hardcoded year list this task
# collapses. Now derived from the single canonical source, so extending
# the observed window (as this task also does, to 2021-2025) requires no
# change here at all.
OBSERVED_SEMESTERS = [(year, sem) for year in OBSERVED_ACADEMIC_YEARS for sem in (1, 2)]

REQUIRED_COLUMNS = {
    "college": ["college_id", "college_name"],
    "program": ["program_id", "program_name", "college_id", "program_level", "nominal_duration_years"],
    "student": ["student_id", "cohort_academic_year", "gender", "birth_year", "home_province",
                "admission_type", "entry_year_level", "entry_college_id", "entry_program_id"],
    "enrollment": ["student_id", "academic_year", "semester_number", "college_id", "program_id",
                   "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"],
    "graduation": ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                   "years_to_complete"],
    "dropout": ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                "dropout_reason", "semesters_completed_before_dropout"],
    "shifter": ["student_id", "academic_year", "semester_number", "from_program_id", "to_program_id"],
}
# NOTE: the source CSVs use `semester_number` (int, 1 or 2) -- confirmed
# against the real data_generator output and against every downstream
# consumer (pipelines/silver/validate_and_dedupe.py, pipelines/gold/*).
# An earlier version of this dict required a `semester_name` column that
# never existed in any source file, which meant every semester-scoped
# ingestion call (enrollment/graduation/dropout/shifter) failed
# _validate_file_level unconditionally. Fixed here rather than papered
# over with a rename in _normalize_column_names, since Bronze must
# describe the column that was actually received, not one that was
# merely planned.


class IngestionError(InvalidSchemaError):
    """Raised for file-level ingestion problems: missing source, empty
    file, or missing expected columns -- now InvalidSchemaError (Task
    46), since this is exactly a "the file's shape doesn't match what
    Bronze requires" failure."""

    def __init__(self, message: str, *, stage: str = "Bronze Ingestion", **kwargs):
        super().__init__(message, stage=stage, **kwargs)

def _inspect_schema(df: pd.DataFrame, entity: str, source_path: str) -> Dict[str, object]:
    """Stage 2: a non-raising snapshot of what a source file actually
    looks like, taken BEFORE normalization or validation touch it. Pure
    observability -- 'what columns/dtypes/row-count did we receive?' --
    never a gate. Missing-column enforcement is _validate_file_level's
    job, further down the pipeline; this function's only responsibility
    is to produce a diagnosable snapshot even for a broken file, which is
    exactly why it must never raise.
    """
    return {
        "entity": entity,
        "source_path": source_path,
        "row_count": len(df),
        "columns": list(df.columns),
        "dtypes": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
    }


def _normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Stage 3: header hygiene ONLY -- trims incidental whitespace and
    lowercases column NAMES so a source system's cosmetic quirks
    (' Student_ID', 'StudentId', 'student_id ') don't masquerade as a
    missing-column failure at validation. Deliberately does NOT touch
    cell VALUES (that's Silver's job -- pipelines/silver/cleaning_rules.py)
    and does NOT rename one business column to another (e.g. mapping a
    synonym onto student_id): that is a data-modeling decision that
    belongs in a reviewable, tested Silver mapping rule, not something
    silently applied at ingestion time. Bronze must still preserve what
    was received; only whitespace/casing of the header row is fair game.
    """
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


# AFTER
def _validate_file_level(df: pd.DataFrame, entity: str, source_path: str) -> None:
    if df.empty:
        raise IngestionError(f"Source is empty: {source_path}", entity=entity, rows_affected=0)
    missing_cols = set(REQUIRED_COLUMNS[entity]) - set(df.columns)
    if missing_cols:
        raise IngestionError(
            f"{source_path} is missing expected column(s) for entity {entity!r}: {sorted(missing_cols)}",
            entity=entity, rows_affected=len(df),
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
        # AFTER
        record_run(
            meta_conn, run_id, batch_id, SCHEMA_VALIDATION_STAGE, entity, partition_key,
            started_at, status="FAILED", rows_in=len(df), source_path=source_path, error_message=summary,
            error_category="INVALID_SCHEMA", rows_affected=failure_count,
        )
        return {"entity": entity, "partition_key": partition_key, "status": "SCHEMA_INVALID",
                "violation_count": failure_count, "summary": summary}


def _bronze_partition_prefix(entity: str, partition_key: str) -> str:
    """Prefix under which every batch for this (entity, partition_key)
    lives, e.g. 'bronze/enrollment/academic_year=2021/semester=1/'.
    Deliberately excludes batch_id -- existence is checked at the
    partition level, not for one specific historical batch."""
    return f"bronze/{entity}/{partition_key}/"


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
    load, validate, stamp, write, log. Returns a small summary dict.

    Root-cause fix (post-mortem: acceptance run 2026-08-19): the
    idempotency check used to be `has_successful_run()` alone -- a query
    against `meta.duckdb`'s run history, which is a LOCAL file untouched
    by `docker compose down`/`up` or a fresh MinIO volume. That let the
    guard report "already ingested" from a *previous* environment's
    history while the *current* storage backend held nothing, so
    `ingest_one` would skip the real write, `bronze` would report
    SUCCESS, and every downstream stage would fail against empty Bronze
    data several steps later with a confusing NoSuchKey error instead of
    a clear one here.

    The guard now requires BOTH the log to say "success" AND the
    configured storage backend to actually contain an object for this
    partition. If the log says yes but storage says no, the two have
    drifted -- treat it as NOT yet ingested and do the real write,
    rather than trusting history that no longer matches physical state.
    """
    if not force and has_successful_run(meta_conn, STAGE, entity, partition_key):
        if storage.list_keys(_bronze_partition_prefix(entity, partition_key)):
            return {"entity": entity, "partition_key": partition_key, "status": "SKIPPED_ALREADY_INGESTED"}
        # Log/storage drift: meta.duckdb believes this succeeded, but the
        # storage backend currently configured (STORAGE_BACKEND) has no
        # object under this prefix. Don't trust stale history -- ingest
        # for real, and make the drift visible in the returned status
        # rather than silently overwriting log_says_done with a fresh
        # write and hoping nobody checks.
        record_run(
            meta_conn, str(uuid.uuid4()), batch_id, STAGE, entity, partition_key,
            datetime.now(timezone.utc), status="DRIFT_DETECTED",
            error_message=(
                "pipeline_run_log has a prior SUCCESS for this partition, but the "
                "configured storage backend has no object at "
                f"{_bronze_partition_prefix(entity, partition_key)!r} -- "
                "re-ingesting instead of skipping."
            ),
        )

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    try:
        # 1. READ
        df = load_fn()

        # 2. INSPECT SCHEMA -- observability snapshot, never raises
        schema_report = _inspect_schema(df, entity, source_path)

        # 3. NORMALIZE COLUMNS -- header hygiene only, never values
        df = _normalize_column_names(df)

        # 4. VALIDATE -- the gate. Raises IngestionError -> no write happens.
        _validate_file_level(df, entity, source_path)
        rows_in = len(df)

        # 5. WRITE BRONZE -- only reached once validation has passed
        df = _stamp_audit_columns(df, batch_id, source_path)
        key = _bronze_key(entity, partition_key, batch_id)
        _write_parquet(storage, key, df)
        record_success_once(
            meta_conn, run_id, batch_id, STAGE, entity, partition_key,
            started_at, rows_in=rows_in, rows_out=len(df), source_path=source_path,
        )
        schema_result = _run_schema_validation(meta_conn, batch_id, entity, partition_key, df, source_path)
        return {"entity": entity, "partition_key": partition_key, "status": "SUCCESS",
                "rows": rows_in, "key": key, "schema_validation": schema_result["status"],
                "schema_report": schema_report}
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
    storage = storage or load_storage_from_env(DEFAULT_BRONZE_STORAGE_PATH, "MINIO_BRONZE_BUCKET")
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
        year_dir = academic_year_label(year)
        sem_dir = semester_label_from_number(sem)
        for entity in SEMESTER_SCOPED_ENTITIES:
            source_path = data_generator_output / year_dir / sem_dir / f"{entity}.csv"
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
    import uuid as _uuid
    from pipelines.common.logging_config import PipelineStageLogger, get_logger

    _logger = get_logger(__name__)
    _run_id = str(_uuid.uuid4())
    with PipelineStageLogger(_run_id, stage="ingestion") as stage_log:
        summary = ingest_all()
        counts: Dict[str, int] = {}
        for r in summary:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        stage_log.rows_processed = sum(
            r.get("rows", 0) for r in summary if r.get("status") == "SUCCESS"
        )
        stage_log.rows_rejected = sum(
            count for status, count in counts.items() if status == "FAILED"
        )
        _logger.info("Bronze ingestion complete: %s", counts, extra={"pipeline_extra": counts})