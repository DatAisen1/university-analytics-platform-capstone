"""
tests/unit/test_audit_bronze.py

Tests for pipelines/ingestion/audit_bronze.py. The audit must agree with
physical reality even when pipeline_run_log and the storage backend
disagree -- that disagreement is exactly the bug class this module
exists to catch (a log row claiming SUCCESS with no real object behind
it, or an object that landed but is empty/truncated).
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from pipelines.common.config import load_default_reference_data
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage
from pipelines.ingestion.audit_bronze import (
    STATUS_EMPTY_OBJECT,
    STATUS_MISSING_OBJECT,
    STATUS_OK,
    audit_bronze,
)
from pipelines.ingestion.ingest_to_bronze import ingest_all


@pytest.fixture
def fixture_output_dir(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pd.DataFrame([{
        "student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": 1,
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS",
    }]).to_csv(output_dir / "student_master.csv", index=False)
    return output_dir


def test_audit_reports_ok_for_every_genuinely_ingested_object(fixture_output_dir, tmp_path):
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    ingest_all(
        storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir,
        reference=load_default_reference_data(),
    )

    results = audit_bronze(storage=storage, meta_conn=meta_conn)

    assert len(results) > 0  # college, program, student all logged SUCCESS
    assert all(r.status == STATUS_OK for r in results)
    assert all(r.size_bytes and r.size_bytes > 0 for r in results)
    assert all(r.last_modified is not None for r in results)


def test_audit_flags_missing_object_when_log_claims_success_but_nothing_was_written(tmp_path):
    """The exact Task 19 failure mode: pipeline_run_log says SUCCESS, but
    no object was ever physically written for that key."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    record_run(
        meta_conn, run_id="r1", batch_id="ghost-batch", stage="bronze_ingestion",
        entity="student", partition_key="all", started_at=datetime.now(timezone.utc),
        status="SUCCESS", rows_in=1, rows_out=1, source_path="students.csv",
    )

    results = audit_bronze(storage=storage, meta_conn=meta_conn)

    assert len(results) == 1
    assert results[0].status == STATUS_MISSING_OBJECT
    assert results[0].size_bytes is None


def test_audit_flags_empty_object(tmp_path):
    """A zero-byte object at the expected key -- e.g. a truncated write --
    must be distinguished from a genuinely healthy Bronze file."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    key = "bronze/student/all/batch_id=b1/data.parquet"
    storage.write_bytes(key, b"")
    record_run(
        meta_conn, run_id="r1", batch_id="b1", stage="bronze_ingestion",
        entity="student", partition_key="all", started_at=datetime.now(timezone.utc),
        status="SUCCESS", rows_in=1, rows_out=1, source_path="students.csv",
    )

    results = audit_bronze(storage=storage, meta_conn=meta_conn)

    assert results[0].status == STATUS_EMPTY_OBJECT
    assert results[0].size_bytes == 0


def test_audit_ignores_failed_and_skipped_runs(tmp_path):
    """Only logged SUCCESS rows are audited -- a FAILED or
    SKIPPED_ALREADY_INGESTED row never claimed a Bronze write happened,
    so there is nothing to verify."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    record_run(
        meta_conn, run_id="r1", batch_id="b1", stage="bronze_ingestion",
        entity="student", partition_key="all", started_at=datetime.now(timezone.utc),
        status="FAILED", source_path="students.csv", error_message="missing column",
    )

    assert audit_bronze(storage=storage, meta_conn=meta_conn) == []