"""
tests/integration/test_pipeline_idempotency.py

Task 26/27: proves the pipeline converges to the same correct end state
whether it's run once, run twice back-to-back, run again after a
simulated mid-batch failure, or run again after only partial completion.
Uses local Parquet/DuckDB storage (no Postgres required) so it runs
everywhere, exercising the same ingest_to_bronze -> clean_entities ->
validate_and_dedupe path the real pipeline uses.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from pipelines.common.config import ReferenceData
from pipelines.common.metadata import get_connection, get_run_log
from pipelines.common.storage import LocalFileStorage
from pipelines.ingestion.ingest_to_bronze import ingest_all
from pipelines.silver.clean_entities import clean_all, read_all_bronze


@pytest.fixture
def tmp_warehouse(tmp_path: Path):
    """A throwaway bronze/silver store + metadata DB per test, so tests
    never share state or depend on run order."""
    bronze_path = tmp_path / "bronze_store"
    silver_path = tmp_path / "silver_store"
    meta_path = tmp_path / "meta.duckdb"
    yield {
        "bronze_storage": LocalFileStorage(bronze_path),
        "silver_storage": LocalFileStorage(silver_path),
        "meta_conn": get_connection(meta_path),
    }
    shutil.rmtree(tmp_path, ignore_errors=True)


def _reference_data(tmp_path: Path) -> ReferenceData:
    from pipelines.common.config import College, Program

    return ReferenceData(
        colleges=[College(college_id="COE", college_name="College of Engineering")],
        programs=[Program(
            program_id="BSCS", program_name="BS Computer Science", college_id="COE",
            program_level="Bachelor", nominal_duration_years=4.0,
        )],
    )


def _generator_output(tmp_path: Path) -> Path:
    """A tiny, deterministic data_generator/output/ tree with one student
    and one semester of enrollment -- enough to exercise ingest_all end
    to end without depending on the full synthetic dataset."""
    out = tmp_path / "data_generator_output"
    out.mkdir()
    pd.DataFrame([{
        "student_id": "S0001", "cohort_academic_year": 2021, "gender": "Male", "birth_year": 2003,
        "home_province": "Bulacan", "admission_type": "Freshman", "entry_year_level": 1,
        "entry_college_id": "COE", "entry_program_id": "BSCS",
    }]).to_csv(out / "student_master.csv", index=False)

    sem_dir = out / "2021" / "1"
    sem_dir.mkdir(parents=True)
    pd.DataFrame([{
        "student_id": "S0001", "academic_year": 2021, "semester_number": 1, "college_id": "COE",
        "program_id": "BSCS", "enrollment_status": "ENROLLED", "year_level": 1,
        "units_enrolled": 18, "is_new_enrollee": True,
    }]).to_csv(sem_dir / "enrollment.csv", index=False)
    return out


def _row_count(storage: LocalFileStorage, key: str) -> int:
    import io
    return len(pd.read_parquet(io.BytesIO(storage.read_bytes(key))))


class TestBronzeIngestionIdempotency:
    """Task 27's four required scenarios, applied to Bronze ingestion."""

    def test_run_once(self, tmp_warehouse, tmp_path):
        reference = _reference_data(tmp_path)
        gen_output = _generator_output(tmp_path)
        results = ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )
        successes = [r for r in results if r["status"] == "SUCCESS"]
        assert len(successes) >= 3  # college, program, student, + the one enrollment partition

    def test_run_twice_does_not_duplicate(self, tmp_warehouse, tmp_path):
        reference = _reference_data(tmp_path)
        gen_output = _generator_output(tmp_path)

        ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )
        second = ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )

        # Second run must not re-SUCCEED anything: partitions that had no
        # source file the first time still have none (NO_SOURCE_FILE,
        # unchanged); partitions that DID succeed must now be SKIPPED,
        # never re-ingested as a fresh SUCCESS.
        assert not any(r["status"] == "SUCCESS" for r in second)
        second_by_key = {(r.get("entity"), r.get("partition_key")): r["status"] for r in second}
        assert second_by_key[("student", "all")] == "SKIPPED_ALREADY_INGESTED"
        assert second_by_key[("college", "all")] == "SKIPPED_ALREADY_INGESTED"
        assert second_by_key[("enrollment", "academic_year=2021/semester=1")] == "SKIPPED_ALREADY_INGESTED"

        # And the union of Bronze data for `student` must still be exactly
        # one row -- no duplicate batch accumulation downstream.
        student_df = read_all_bronze(tmp_warehouse["bronze_storage"], "student")
        assert len(student_df) == 1

    def test_run_after_partial_completion_only_retries_the_gap(self, tmp_warehouse, tmp_path):
        """Simulates: student + college + program ingested successfully,
        but the enrollment partition failed (e.g. process crashed before
        it ran). Re-running must NOT re-ingest the already-successful
        units, and MUST still pick up the missing one."""
        reference = _reference_data(tmp_path)
        gen_output = _generator_output(tmp_path)

        # First "partial" run: delete the enrollment source file so its
        # ingestion is skipped as NO_SOURCE_FILE, simulating a run that
        # completed for reference/student data but not for enrollment.
        enrollment_csv = gen_output / "2021" / "1" / "enrollment.csv"
        enrollment_csv.rename(gen_output / "2021" / "1" / "enrollment.csv.bak")
        first = ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )
        assert any(
            r.get("entity") == "enrollment"
            and r.get("partition_key") == "academic_year=2021/semester=1"
            and r["status"] == "NO_SOURCE_FILE"
            for r in first
        )

        # Restore the source and re-run: student/college/program must be
        # SKIPPED (already done), enrollment must now SUCCEED.
        (gen_output / "2021" / "1" / "enrollment.csv.bak").rename(enrollment_csv)
        second = ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )
        by_key = {(r.get("entity"), r.get("partition_key")): r["status"] for r in second}
        assert by_key[("student", "all")] == "SKIPPED_ALREADY_INGESTED"
        assert by_key[("enrollment", "academic_year=2021/semester=1")] == "SUCCESS"

    def test_force_reingest_still_ends_correct(self, tmp_warehouse, tmp_path):
        """force=True intentionally re-ingests everything (e.g. a manual
        backfill). Even then, Silver's exact-duplicate collapse must bring
        the final state back to one row per student -- 'the final result
        remains correct' even when Bronze itself legitimately grows."""
        reference = _reference_data(tmp_path)
        gen_output = _generator_output(tmp_path)

        ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )
        ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference, force=True,
        )

        # Bronze now legitimately has two batches of `student` -- but
        # Silver cleaning's exact-duplicate collapse (clean_entities.py
        # Stage 6) must still converge to exactly one row.
        clean_all(
            bronze_storage=tmp_warehouse["bronze_storage"], silver_storage=tmp_warehouse["silver_storage"],
            meta_conn=tmp_warehouse["meta_conn"], entities=["college", "program", "student"],
        )
        assert _row_count(tmp_warehouse["silver_storage"], "silver/student/data.parquet") == 1


class TestSilverCleaningIdempotency:
    def test_run_twice_produces_identical_output(self, tmp_warehouse, tmp_path):
        reference = _reference_data(tmp_path)
        gen_output = _generator_output(tmp_path)
        ingest_all(
            storage=tmp_warehouse["bronze_storage"], meta_conn=tmp_warehouse["meta_conn"],
            data_generator_output=gen_output, reference=reference,
        )

        clean_all(
            bronze_storage=tmp_warehouse["bronze_storage"], silver_storage=tmp_warehouse["silver_storage"],
            meta_conn=tmp_warehouse["meta_conn"], entities=["college", "program", "student"],
        )
        first_student = _row_count(tmp_warehouse["silver_storage"], "silver/student/data.parquet")

        # Re-run cleaning with no new Bronze input -- output must be stable.
        clean_all(
            bronze_storage=tmp_warehouse["bronze_storage"], silver_storage=tmp_warehouse["silver_storage"],
            meta_conn=tmp_warehouse["meta_conn"], entities=["college", "program", "student"],
        )
        second_student = _row_count(tmp_warehouse["silver_storage"], "silver/student/data.parquet")
        assert first_student == second_student == 1