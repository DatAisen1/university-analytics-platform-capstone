"""
tests/unit/test_metadata.py

Tests for pipelines/common/metadata.py -- the pipeline_run_log store and
its idempotency check (has_successful_run), which is the mechanism that
makes Bronze ingestion safe to re-run.
"""

from datetime import datetime, timezone

from pipelines.common.metadata import get_connection, has_successful_run, record_run, get_run_log


def test_has_successful_run_false_when_no_log_exists(tmp_path):
    conn = get_connection(tmp_path / "meta.duckdb")
    assert has_successful_run(conn, "bronze_ingestion", "student", "all") is False


def test_record_run_then_has_successful_run_true(tmp_path):
    conn = get_connection(tmp_path / "meta.duckdb")
    record_run(
        conn, run_id="r1", batch_id="b1", stage="bronze_ingestion", entity="student",
        partition_key="all", started_at=datetime.now(timezone.utc), status="SUCCESS",
        rows_in=100, rows_out=100, source_path="student_master.csv",
    )
    assert has_successful_run(conn, "bronze_ingestion", "student", "all") is True


def test_failed_run_does_not_count_as_successful(tmp_path):
    conn = get_connection(tmp_path / "meta.duckdb")
    record_run(
        conn, run_id="r1", batch_id="b1", stage="bronze_ingestion", entity="student",
        partition_key="all", started_at=datetime.now(timezone.utc), status="FAILED",
        error_message="file not found",
    )
    assert has_successful_run(conn, "bronze_ingestion", "student", "all") is False


def test_has_successful_run_is_scoped_to_exact_entity_and_partition(tmp_path):
    conn = get_connection(tmp_path / "meta.duckdb")
    record_run(
        conn, run_id="r1", batch_id="b1", stage="bronze_ingestion", entity="enrollment",
        partition_key="academic_year=2021/semester=1", started_at=datetime.now(timezone.utc),
        status="SUCCESS",
    )
    # Same stage/entity, different partition -- should NOT be considered done
    assert has_successful_run(conn, "bronze_ingestion", "enrollment", "academic_year=2021/semester=2") is False
    # Same partition, different entity -- should NOT be considered done
    assert has_successful_run(conn, "bronze_ingestion", "dropout", "academic_year=2021/semester=1") is False
    # Exact match -- should be done
    assert has_successful_run(conn, "bronze_ingestion", "enrollment", "academic_year=2021/semester=1") is True


def test_get_run_log_returns_all_recorded_runs(tmp_path):
    conn = get_connection(tmp_path / "meta.duckdb")
    for i in range(3):
        record_run(
            conn, run_id=f"r{i}", batch_id="b1", stage="bronze_ingestion", entity="student",
            partition_key="all", started_at=datetime.now(timezone.utc), status="SUCCESS",
        )
    log = get_run_log(conn)
    assert len(log) == 3


def test_connection_persists_across_reopen(tmp_path):
    """A real requirement: pipeline_run_log must survive between separate
    script invocations (e.g. ingestion run today, re-run tomorrow) -- it's
    a file-backed DB, not an in-memory one that resets each process."""
    db_path = tmp_path / "meta.duckdb"
    conn1 = get_connection(db_path)
    record_run(
        conn1, run_id="r1", batch_id="b1", stage="bronze_ingestion", entity="student",
        partition_key="all", started_at=datetime.now(timezone.utc), status="SUCCESS",
    )
    conn1.close()

    conn2 = get_connection(db_path)
    assert has_successful_run(conn2, "bronze_ingestion", "student", "all") is True
