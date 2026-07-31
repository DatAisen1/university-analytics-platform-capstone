"""
pipelines/ingestion/audit_bronze.py

`Ensure Bronze Actually Exists`: an independent, read-only audit that
answers "does the data lake actually contain what pipeline_run_log
claims it does?" as a check SEPARATE from ingest_to_bronze.py's own
bookkeeping, on purpose. A script exiting 0 and logging a SUCCESS row
only proves ingest_one's write call returned without raising -- it does
NOT prove the object is still physically there, non-empty, and at the
key it's supposed to be at. (Concretely: this repo's `warehouse/bronze_
store/` already contained Parquet files that a prior, buggy version of
the ingestion code could not have produced against today's real
source data -- proof that "files exist in the directory" and "the
current ingestion code works" are two different claims.)

This module re-derives the expected object key for every logged SUCCESS
and checks it against the storage backend directly via `ObjectStorage.
stat()` -- bucket, key (path + file name), size, and last-modified
timestamp -- rather than trusting the log alone.

Run via: python -m pipelines.ingestion.audit_bronze
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import duckdb

from pipelines.common.metadata import get_connection
from pipelines.common.storage import LocalFileStorage, ObjectStorage
from pipelines.ingestion.ingest_to_bronze import (
    DEFAULT_BRONZE_STORAGE_PATH,
    STAGE,
    _bronze_key,
)

# Possible per-object audit outcomes.
STATUS_OK = "OK"
STATUS_MISSING_OBJECT = "MISSING_OBJECT"
STATUS_EMPTY_OBJECT = "EMPTY_OBJECT"


@dataclass(frozen=True)
class BronzeAuditResult:
    """One audited (entity, partition_key, batch_id): what pipeline_run_log
    claims vs. what the storage backend actually has."""

    entity: str
    partition_key: str
    batch_id: str
    key: str
    status: str  # OK | MISSING_OBJECT | EMPTY_OBJECT
    bucket: Optional[str]
    size_bytes: Optional[int]
    last_modified: Optional[str]
    rows_logged: int


def audit_bronze(
    storage: Optional[ObjectStorage] = None,
    meta_conn: Optional[duckdb.DuckDBPyConnection] = None,
) -> List[BronzeAuditResult]:
    """For every pipeline_run_log row where stage == bronze_ingestion and
    status == 'SUCCESS', re-derive the expected object key and verify it
    against the storage backend directly. Returns one BronzeAuditResult
    per logged success, in log order, so the caller can filter for
    problems (`status != "OK"`) without re-deriving keys itself.
    """
    storage = storage or LocalFileStorage(DEFAULT_BRONZE_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    logged_successes = meta_conn.execute(
        "SELECT batch_id, entity, partition_key, rows_out "
        "FROM pipeline_run_log WHERE stage = ? AND status = 'SUCCESS' "
        "ORDER BY started_at",
        [STAGE],
    ).fetchall()

    results: List[BronzeAuditResult] = []
    for batch_id, entity, partition_key, rows_out in logged_successes:
        key = _bronze_key(entity, partition_key, batch_id)
        try:
            meta = storage.stat(key)
        except FileNotFoundError:
            results.append(BronzeAuditResult(
                entity=entity, partition_key=partition_key, batch_id=batch_id, key=key,
                status=STATUS_MISSING_OBJECT, bucket=None, size_bytes=None, last_modified=None,
                rows_logged=rows_out,
            ))
            continue

        status = STATUS_EMPTY_OBJECT if meta.size_bytes == 0 else STATUS_OK
        results.append(BronzeAuditResult(
            entity=entity, partition_key=partition_key, batch_id=batch_id, key=key,
            status=status, bucket=meta.bucket, size_bytes=meta.size_bytes,
            last_modified=meta.last_modified.isoformat(), rows_logged=rows_out,
        ))

    if owns_conn:
        meta_conn.close()
    return results


if __name__ == "__main__":
    audit_results = audit_bronze()
    problems = [r for r in audit_results if r.status != STATUS_OK]

    print(f"Bronze audit: {len(audit_results)} logged SUCCESS write(s) checked, "
          f"{len(problems)} problem(s) found.")
    for r in problems:
        print(f"  [{r.status}] entity={r.entity} partition={r.partition_key} "
              f"batch={r.batch_id} key={r.key}")
    if not problems:
        print("  Every logged Bronze write is physically present, non-empty, and verified.")