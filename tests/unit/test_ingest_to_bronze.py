"""
tests/unit/test_ingest_to_bronze.py

Integration tests for pipelines/ingestion/ingest_to_bronze.py, run against
a small hand-built fixture population (not the full 7,800-student
dataset -- fast, deterministic, and independent of Days 4-6's generator).
"""

import io

import pandas as pd
import pytest

from pipelines.common.config import load_default_reference_data
from pipelines.common.metadata import get_connection, get_run_log
from pipelines.common.storage import LocalFileStorage
from pipelines.ingestion.ingest_to_bronze import ingest_all, ingest_one, IngestionError, _validate_file_level


REFERENCE = load_default_reference_data()


@pytest.fixture
def fixture_output_dir(tmp_path):
    """A minimal but structurally complete data_generator output tree:
    student_master.csv + one semester partition with all four entities."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    students = pd.DataFrame([
        {"student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
         "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": 1,
         "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS"},
        {"student_id": "2021-00002", "cohort_academic_year": 2021, "gender": "Female", "birth_year": 2003,
         "home_province": "Bulacan", "admission_type": "Freshman", "entry_year_level": 1,
         "entry_college_id": "IPE", "entry_program_id": "IPE-CERT-PE"},
    ])
    students.to_csv(output_dir / "student_master.csv", index=False)

    partition_dir = output_dir / "2021-2022" / "1st Semester"
    partition_dir.mkdir(parents=True)

    enrollment = pd.DataFrame([
        {"student_id": "2021-00001", "academic_year": 2021, "semester_number": 1, "college_id": "CICT",
         "program_id": "CICT-BSDS", "enrollment_status": "ENROLLED", "year_level": 1,
         "units_enrolled": 18, "is_new_enrollee": True},
        {"student_id": "2021-00002", "academic_year": 2021, "semester_number": 1, "college_id": "IPE",
         "program_id": "IPE-CERT-PE", "enrollment_status": "ENROLLED", "year_level": 1,
         "units_enrolled": 15, "is_new_enrollee": True},
    ])
    enrollment.to_csv(partition_dir / "enrollment.csv", index=False)
    # Deliberately no graduation.csv/dropout.csv/shifter.csv in this partition --
    # exercises the NO_SOURCE_FILE path, same as the real first semester.

    return output_dir


def test_ingest_all_succeeds_and_skips_missing_entities(fixture_output_dir, tmp_path):
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    results = ingest_all(
        storage=storage, meta_conn=meta_conn,
        data_generator_output=fixture_output_dir, reference=REFERENCE,
    )

    statuses = {(r["entity"], r["partition_key"]): r["status"] for r in results}
    assert statuses[("college", "all")] == "SUCCESS"
    assert statuses[("program", "all")] == "SUCCESS"
    assert statuses[("student", "all")] == "SUCCESS"
    assert statuses[("enrollment", "academic_year=2021/semester=1")] == "SUCCESS"
    # No source file for these three in this fixture -- correctly reported, not crashed
    assert statuses[("graduation", "academic_year=2021/semester=1")] == "NO_SOURCE_FILE"
    assert statuses[("dropout", "academic_year=2021/semester=1")] == "NO_SOURCE_FILE"
    assert statuses[("shifter", "academic_year=2021/semester=1")] == "NO_SOURCE_FILE"


def test_ingested_data_has_audit_columns(fixture_output_dir, tmp_path):
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")
    ingest_all(storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir, reference=REFERENCE)

    keys = storage.list_keys("bronze/student")
    assert len(keys) == 1
    df = pd.read_parquet(io.BytesIO(storage.read_bytes(keys[0])))

    assert "_ingested_at" in df.columns
    assert "_source_file" in df.columns
    assert "_batch_id" in df.columns
    assert len(df) == 2  # both students, untouched
    assert df["_batch_id"].nunique() == 1  # same batch stamps every row in one file


def test_rerun_without_force_is_idempotent(fixture_output_dir, tmp_path):
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    ingest_all(storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir, reference=REFERENCE)
    files_after_first_run = sorted(storage.list_keys("bronze"))

    results_second_run = ingest_all(
        storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir, reference=REFERENCE,
    )
    files_after_second_run = sorted(storage.list_keys("bronze"))

    assert files_after_first_run == files_after_second_run  # zero new files written
    success_statuses = {r["status"] for r in results_second_run if r["status"] != "NO_SOURCE_FILE"}
    assert success_statuses == {"SKIPPED_ALREADY_INGESTED"}


def test_rerun_against_wiped_storage_reingests_instead_of_skipping(fixture_output_dir, tmp_path):
    """Regression test for the 2026-08-19 acceptance-test incident: if
    meta.duckdb's run history says a partition was already ingested but
    the object storage backend actually has nothing there (e.g. a fresh
    MinIO volume, or a wiped bucket, while meta.duckdb -- a local file --
    survived), `ingest_one` must NOT trust the stale log and skip. It
    must detect the drift and actually re-ingest, so Bronze ends up
    correct instead of silently empty."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    # First run: populates both meta.duckdb's history AND storage.
    ingest_all(storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir, reference=REFERENCE)
    assert storage.list_keys("bronze/student")  # sanity: something is really there

    # Simulate a wiped/rebuilt storage backend (fresh MinIO volume) while
    # meta.duckdb's run history is untouched -- exactly what a
    # `docker compose down -v && docker compose up` between sessions does.
    wiped_storage = LocalFileStorage(tmp_path / "bronze_store_fresh")

    results = ingest_all(
        storage=wiped_storage, meta_conn=meta_conn,
        data_generator_output=fixture_output_dir, reference=REFERENCE,
    )

    statuses = {(r["entity"], r["partition_key"]): r["status"] for r in results}
    # Despite meta.duckdb showing prior SUCCESS for every one of these,
    # the guard must detect that `wiped_storage` has no object and
    # actually re-ingest -- not report SKIPPED_ALREADY_INGESTED.
    assert statuses[("student", "all")] == "SUCCESS"
    assert statuses[("college", "all")] == "SUCCESS"
    assert statuses[("enrollment", "academic_year=2021/semester=1")] == "SUCCESS"
    assert wiped_storage.list_keys("bronze/student")  # and it's really written this time


def test_force_true_reprocesses_and_appends_new_batch_file(fixture_output_dir, tmp_path):
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    ingest_all(storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir, reference=REFERENCE)
    count_after_first = len(storage.list_keys("bronze/student"))

    ingest_all(
        storage=storage, meta_conn=meta_conn, data_generator_output=fixture_output_dir,
        reference=REFERENCE, force=True,
    )
    count_after_forced_rerun = len(storage.list_keys("bronze/student"))

    # force=True intentionally reprocesses -- Bronze never overwrites, so this
    # APPENDS a new batch-tagged file rather than replacing the old one.
    assert count_after_forced_rerun == count_after_first + 1


# ---------------------------------------------------------------------------
# _validate_file_level -- file-level checks in isolation
# ---------------------------------------------------------------------------

def test_validate_file_level_rejects_empty_dataframe():
    with pytest.raises(IngestionError, match="empty"):
        _validate_file_level(pd.DataFrame(), "student", "fake_path.csv")


def test_validate_file_level_rejects_missing_columns():
    df = pd.DataFrame([{"student_id": "2021-00001"}])  # missing every other required column
    with pytest.raises(IngestionError, match="missing expected column"):
        _validate_file_level(df, "student", "fake_path.csv")


def test_validate_file_level_accepts_well_formed_dataframe():
    df = pd.DataFrame([{
        "student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": 1,
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS",
    }])
    _validate_file_level(df, "student", "fake_path.csv")  # should not raise


def test_ingest_one_records_failure_without_crashing(tmp_path):
    """A file-level validation failure for one entity should be caught,
    logged as FAILED, and returned as a result -- not raised up and out
    (which would abort every OTHER entity's ingestion in the same run)."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    def broken_loader():
        return pd.DataFrame()  # empty -- will fail _validate_file_level

    result = ingest_one(
        storage, meta_conn, batch_id="b1", entity="student", partition_key="all",
        source_path="broken.csv", load_fn=broken_loader,
    )
    assert result["status"] == "FAILED"
    assert "empty" in result["error"]


def test_ingest_one_runs_schema_validation_and_reports_it(tmp_path):
    """Day 9: schema validation runs as a post-write step and its result
    is surfaced in ingest_one's return value, logged to pipeline_run_log
    under a distinct stage -- without blocking the Bronze write itself."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    valid_students = pd.DataFrame([{
        # cohort_academic_year is the source's raw label ("2021-2022"), not a bare
        # int -- see data_generator/generators/generate_students.py's
        # academic_year_label(cohort_year) and STUDENT_SCHEMA in
        # pipelines/common/schemas.py. An earlier version of this fixture used a
        # bare int here, which made this the one test asserting SCHEMA_VALID
        # fail against the schema it was meant to exercise.
        "student_id": "2021-00001", "cohort_academic_year": "2021-2022", "gender": "Male", "birth_year": 2003,
        "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": 1,
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS",
    }])

    result = ingest_one(
        storage, meta_conn, batch_id="b1", entity="student", partition_key="all",
        source_path="students.csv", load_fn=lambda: valid_students,
    )
    assert result["status"] == "SUCCESS"
    assert result["schema_validation"] == "SCHEMA_VALID"

    log = get_run_log(meta_conn)
    schema_rows = log[log["stage"] == "bronze_schema_validation"]
    assert len(schema_rows) == 1
    assert schema_rows.iloc[0]["status"] == "SUCCESS"


def test_ingest_one_reports_schema_invalid_but_bronze_write_still_succeeds(tmp_path):
    """The core Day 9 design point: Bronze must land the data regardless
    of schema violations -- only the validation REPORT should fail, not
    the ingestion itself. Bronze's job is preserving what was received."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    invalid_students = pd.DataFrame([{
        "student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male",
        "birth_year": 1850,  # out of the schema's valid range
        "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": 1,
        "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS",
    }])

    result = ingest_one(
        storage, meta_conn, batch_id="b1", entity="student", partition_key="all",
        source_path="students.csv", load_fn=lambda: invalid_students,
    )
    # Bronze write itself still succeeded -- the row landed as-is
    assert result["status"] == "SUCCESS"
    assert result["schema_validation"] == "SCHEMA_INVALID"
    assert storage.exists(result["key"])

    log = get_run_log(meta_conn)
    schema_rows = log[log["stage"] == "bronze_schema_validation"]
    assert schema_rows.iloc[0]["status"] == "FAILED"
    assert "birth_year" in schema_rows.iloc[0]["error_message"]