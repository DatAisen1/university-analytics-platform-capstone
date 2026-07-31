"""
tests/unit/test_clean_entities.py

Integration tests for pipelines/silver/clean_entities.py against a small
fixture Bronze population -- proves the DuckDB SQL orchestration actually
applies the cleaning rules correctly end-to-end, not just that the pure
functions work in isolation (test_cleaning_rules.py already covers that).
"""

import io

import pandas as pd
import pytest

from pipelines.common.metadata import get_connection
from pipelines.common.storage import LocalFileStorage
from pipelines.silver.clean_entities import clean_all, read_all_bronze


def _write_bronze_parquet(storage: LocalFileStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


@pytest.fixture
def fixture_bronze(tmp_path):
    """Bronze data spanning TWO partitions/batches for enrollment (to
    prove the global, cross-partition read), with deliberately messy
    whitespace and status-casing noise baked in."""
    storage = LocalFileStorage(tmp_path / "bronze_store")

    colleges = pd.DataFrame([{"college_id": " COA ", "college_name": " College of Architecture "}])
    _write_bronze_parquet(storage, "bronze/college/all/batch_id=b1/data.parquet", colleges)

    programs = pd.DataFrame([{
        "program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
        "program_level": " Bachelor ", "nominal_duration_years": 5.0,
    }])
    _write_bronze_parquet(storage, "bronze/program/all/batch_id=b1/data.parquet", programs)

    students = pd.DataFrame([{
        "student_id": "2021-00001", "cohort_academic_year": 2021, "gender": "Male",
        "birth_year": 2003, "home_province": "  Nueva Ecija  ", "admission_type": "Freshman",
        "entry_year_level": 1, "entry_college_id": "COA", "entry_program_id": "COA-BSARCH",
    }])
    _write_bronze_parquet(storage, "bronze/student/all/batch_id=b1/data.parquet", students)

    # Partition 1 (2021-1): messy status casing
    enrollment_p1 = pd.DataFrame([
        {"student_id": "2021-00001", "academic_year": 2021, "semester_number": 1,
         "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": " ENROLLED ",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
    ])
    _write_bronze_parquet(
        storage, "bronze/enrollment/academic_year=2021/semester=1/batch_id=b1/data.parquet", enrollment_p1
    )

    # Partition 2 (2021-2): different batch entirely -- proves the global read spans partitions
    enrollment_p2 = pd.DataFrame([
        {"student_id": "2021-00001", "academic_year": 2021, "semester_number": 2,
         "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "enrolled",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    _write_bronze_parquet(
        storage, "bronze/enrollment/academic_year=2021/semester=2/batch_id=b2/data.parquet", enrollment_p2
    )

    return storage


def test_read_all_bronze_unions_across_partitions_and_batches(fixture_bronze):
    df = read_all_bronze(fixture_bronze, "enrollment")
    assert len(df) == 2  # both partitions' rows present
    assert set(df["semester_number"]) == {1, 2}


def test_read_all_bronze_missing_entity_raises_file_not_found(fixture_bronze):
    with pytest.raises(FileNotFoundError, match="No Bronze data found"):
        read_all_bronze(fixture_bronze, "shifter")  # never written in this fixture


def test_clean_all_normalizes_enrollment_status_across_partitions(fixture_bronze, tmp_path):
    silver_storage = LocalFileStorage(tmp_path / "silver_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    results = clean_all(
        bronze_storage=fixture_bronze, silver_storage=silver_storage, meta_conn=meta_conn,
        entities=["college", "program", "student", "enrollment"],
    )

    enrollment_result = next(r for r in results if r["entity"] == "enrollment")
    assert enrollment_result["status"] == "SUCCESS"
    assert enrollment_result["rows"] == 2
    assert enrollment_result["unknown_status_count"] == 0

    silver_df = pd.read_parquet(io.BytesIO(silver_storage.read_bytes("silver/enrollment/data.parquet")))
    # Both ' ENROLLED ' and 'enrolled' must have normalized to the same controlled value
    assert set(silver_df["enrollment_status"]) == {"ENROLLED"}


def test_clean_all_trims_text_fields(fixture_bronze, tmp_path):
    silver_storage = LocalFileStorage(tmp_path / "silver_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    clean_all(
        bronze_storage=fixture_bronze, silver_storage=silver_storage, meta_conn=meta_conn,
        entities=["college", "student"],
    )

    college_df = pd.read_parquet(io.BytesIO(silver_storage.read_bytes("silver/college/data.parquet")))
    assert college_df.iloc[0]["college_id"] == "COA"  # no leading/trailing whitespace
    assert college_df.iloc[0]["college_name"] == "College of Architecture"

    student_df = pd.read_parquet(io.BytesIO(silver_storage.read_bytes("silver/student/data.parquet")))
    assert student_df.iloc[0]["home_province"] == "Nueva Ecija"


def test_clean_all_missing_entity_recorded_as_failed_but_others_still_process(fixture_bronze, tmp_path):
    silver_storage = LocalFileStorage(tmp_path / "silver_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    results = clean_all(
        bronze_storage=fixture_bronze, silver_storage=silver_storage, meta_conn=meta_conn,
        entities=["college", "shifter"],  # shifter has no Bronze data in this fixture
    )
    statuses = {r["entity"]: r["status"] for r in results}
    assert statuses["college"] == "SUCCESS"
    assert statuses["shifter"] == "FAILED"


def test_clean_all_tags_unrecognized_status_as_unknown_not_dropped(tmp_path):
    """A genuinely garbage status value should survive cleaning tagged as
    UNKNOWN, not silently vanish -- Day 11's quarantine step needs to see
    it to make an informed decision."""
    storage = LocalFileStorage(tmp_path / "bronze_store")
    enrollment = pd.DataFrame([{
        "student_id": "2021-00001", "academic_year": 2021, "semester_number": 1,
        "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "SUSPENDED",
        "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True,
    }])
    _write_bronze_parquet(
        storage, "bronze/enrollment/academic_year=2021/semester=1/batch_id=b1/data.parquet", enrollment
    )

    silver_storage = LocalFileStorage(tmp_path / "silver_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")
    results = clean_all(
        bronze_storage=storage, silver_storage=silver_storage, meta_conn=meta_conn, entities=["enrollment"],
    )

    assert results[0]["unknown_status_count"] == 1
    silver_df = pd.read_parquet(io.BytesIO(silver_storage.read_bytes("silver/enrollment/data.parquet")))
    assert silver_df.iloc[0]["enrollment_status"] == "UNKNOWN:SUSPENDED"
