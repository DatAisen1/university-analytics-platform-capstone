"""
tests/unit/test_build_facts.py

Tests for pipelines/gold/build_facts.py. The centerpiece is proving the
AS-OF join actually resolves student_key correctly across a shift event --
a pre-shift enrollment record must point to the OLD dim_student row, a
post-shift record to the NEW one. Getting this wrong would silently make
every pre-shift enrollment record look like it was always in the
post-shift program, defeating the entire point of building SCD2 (Day 12).
"""

import duckdb
import io
import pandas as pd
import pytest

from pipelines.gold.build_dimensions import build_dim_academic_year, build_dim_semester, semester_key_lookup
from pipelines.gold.build_facts import (
    build_all_facts,
    build_fact_dropout,
    build_fact_enrollment,
    build_fact_graduation,
    build_fact_retention,
    build_fact_shifter,
    resolve_student_key_as_of,
)


@pytest.fixture
def dim_semester():
    return build_dim_semester(build_dim_academic_year())


@pytest.fixture
def dims(dim_semester):
    dim_college = pd.DataFrame([{"college_key": 1, "college_id": "CICT", "college_name": "College of ICT"}])
    dim_program = pd.DataFrame([
        {"program_key": 1, "program_id": "CICT-BSIT-WEB", "program_name": "BSIT Web", "college_id": "CICT",
         "college_key": 1, "program_level": "Bachelor", "nominal_duration_years": 4.0},
        {"program_key": 2, "program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
         "college_key": 2, "program_level": "Bachelor", "nominal_duration_years": 5.0},
    ])
    # dim_student: one shifted student (S1, shifts at 2021-2) + one never-shifted (S2)
    sem_key = semester_key_lookup(dim_semester)
    dim_student = pd.DataFrame([
        {"student_key": 1, "student_id": "S1", "gender": "Male", "birth_year": 2003,
         "home_province": "Nueva Ecija", "admission_type": "Freshman",
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB",
         "_valid_from_semester_key": sem_key[(2021, 1)], "_valid_to_semester_key": sem_key[(2021, 1)],
         "_is_current": False},
        {"student_key": 2, "student_id": "S1", "gender": "Male", "birth_year": 2003,
         "home_province": "Nueva Ecija", "admission_type": "Freshman",
         "college_id": "COA", "program_id": "COA-BSARCH",
         "_valid_from_semester_key": sem_key[(2021, 2)], "_valid_to_semester_key": None,
         "_is_current": True},
        {"student_key": 3, "student_id": "S2", "gender": "Female", "birth_year": 2003,
         "home_province": "Bulacan", "admission_type": "Freshman",
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB",
         "_valid_from_semester_key": sem_key[(2021, 1)], "_valid_to_semester_key": None,
         "_is_current": True},
    ])
    return {"dim_student": dim_student, "dim_program": dim_program, "dim_college": dim_college,
            "dim_semester": dim_semester, "dim_academic_year": build_dim_academic_year()}


# ---------------------------------------------------------------------------
# resolve_student_key_as_of -- the centerpiece test
# ---------------------------------------------------------------------------

def test_as_of_join_resolves_pre_shift_record_to_old_student_key(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1,  # BEFORE the shift
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
    ])
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_semester"], conn)
    conn.close()
    assert result.iloc[0]["student_key"] == 1  # the OLD (pre-shift) row


def test_as_of_join_resolves_post_shift_record_to_new_student_key(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 2,  # AFTER the shift
         "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_semester"], conn)
    conn.close()
    assert result.iloc[0]["student_key"] == 2  # the NEW (post-shift) row


def test_as_of_join_resolves_never_shifted_student_consistently(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S2", "academic_year": 2021, "semester_number": 1,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
        {"student_id": "S2", "academic_year": 2024, "semester_number": 2,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 4, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_semester"], conn)
    conn.close()
    assert (result["student_key"] == 3).all()  # same key at every point in time


# ---------------------------------------------------------------------------
# build_fact_enrollment / graduation / dropout / shifter -- row-count reconciliation
# ---------------------------------------------------------------------------

def test_build_fact_enrollment_reconciles_row_count(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
        {"student_id": "S2", "academic_year": 2021, "semester_number": 1,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
    ])
    fact = build_fact_enrollment(enrollment, dims, conn)
    conn.close()
    assert len(fact) == len(enrollment)  # no rows silently dropped by the joins
    assert set(fact.columns) == {"student_key", "program_key", "college_key", "semester_key",
                                   "academic_year_key", "enrollment_status", "year_level",
                                   "units_enrolled", "is_new_enrollee"}


def test_build_fact_graduation_reconciles_row_count(dims):
    conn = duckdb.connect(":memory:")
    graduation = pd.DataFrame([
        {"student_id": "S2", "academic_year": 2024, "semester_number": 2,
         "program_id": "CICT-BSIT-WEB", "college_id": "CICT", "years_to_complete": 4.0},
    ])
    fact = build_fact_graduation(graduation, dims, conn)
    conn.close()
    assert len(fact) == 1
    assert fact.iloc[0]["student_key"] == 3


def test_build_fact_dropout_reconciles_row_count(dims):
    conn = duckdb.connect(":memory:")
    dropout = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1,
         "program_id": "CICT-BSIT-WEB", "college_id": "CICT", "dropout_reason": "Financial",
         "semesters_completed_before_dropout": 0},
    ])
    fact = build_fact_dropout(dropout, dims, conn)
    conn.close()
    assert len(fact) == 1
    assert fact.iloc[0]["student_key"] == 1  # pre-shift key, correctly


def test_build_fact_shifter_resolves_both_program_keys(dims):
    conn = duckdb.connect(":memory:")
    shifter = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 2,
         "from_program_id": "CICT-BSIT-WEB", "to_program_id": "COA-BSARCH"},
    ])
    fact = build_fact_shifter(shifter, dims, conn)
    conn.close()
    assert len(fact) == 1
    assert fact.iloc[0]["from_program_key"] == 1
    assert fact.iloc[0]["to_program_key"] == 2


# ---------------------------------------------------------------------------
# build_fact_retention
# ---------------------------------------------------------------------------

def test_fact_retention_excludes_graduated_rows(dims):
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1,
         "semester_key": dims["dim_semester"].iloc[6]["semester_key"], "academic_year_key": 4,
         "enrollment_status": "GRADUATED", "year_level": 4, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_semester"])
    assert len(fact_retention) == 0  # graduated rows never appear in fact_retention


def test_fact_retention_excludes_final_observed_semester(dims):
    max_key = dims["dim_semester"]["semester_key"].max()
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "semester_key": max_key,
         "academic_year_key": 4, "enrollment_status": "ENROLLED", "year_level": 4,
         "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_semester"])
    assert len(fact_retention) == 0  # 2024-2 has no "next semester" to check


def test_fact_retention_marks_continuing_student_as_retained(dims):
    keys = list(dims["dim_semester"]["semester_key"])
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "semester_key": keys[0],
         "academic_year_key": 1, "enrollment_status": "ENROLLED", "year_level": 1,
         "units_enrolled": 18, "is_new_enrollee": True},
        {"student_key": 3, "program_key": 1, "college_key": 1, "semester_key": keys[1],
         "academic_year_key": 1, "enrollment_status": "ENROLLED", "year_level": 1,
         "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_semester"])
    # Both rows are eligible (neither GRADUATED nor the final observed semester):
    # keys[0] has a following ENROLLED record at keys[1] -> retained.
    # keys[1] has no following record at all (nothing at keys[2]) -> not retained.
    assert len(fact_retention) == 2
    row_at_keys0 = fact_retention[fact_retention["semester_key"] == keys[0]].iloc[0]
    row_at_keys1 = fact_retention[fact_retention["semester_key"] == keys[1]].iloc[0]
    assert row_at_keys0["is_retained"] == 1
    assert row_at_keys1["is_retained"] == 0


def test_fact_retention_marks_dropped_student_as_not_retained(dims):
    keys = list(dims["dim_semester"]["semester_key"])
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "semester_key": keys[0],
         "academic_year_key": 1, "enrollment_status": "ENROLLED", "year_level": 1,
         "units_enrolled": 18, "is_new_enrollee": True},
        # no record at all in the next semester -- this student dropped
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_semester"])
    assert len(fact_retention) == 1
    assert fact_retention.iloc[0]["is_retained"] == 0


# ---------------------------------------------------------------------------
# Idempotency -- run twice against the SAME Silver/Gold state, compare content
# ---------------------------------------------------------------------------

def test_build_all_facts_is_idempotent(tmp_path):
    from pipelines.common.metadata import get_connection
    from pipelines.common.storage import LocalFileStorage

    silver_storage = LocalFileStorage(tmp_path / "silver_store")
    gold_storage = LocalFileStorage(tmp_path / "gold_store")
    meta_conn = get_connection(tmp_path / "meta.duckdb")

    dim_semester = build_dim_semester(build_dim_academic_year())
    dim_college = pd.DataFrame([{"college_key": 1, "college_id": "CICT", "college_name": "College of ICT"}])
    dim_program = pd.DataFrame([{"program_key": 1, "program_id": "CICT-BSIT-WEB", "program_name": "BSIT Web",
                                  "college_id": "CICT", "college_key": 1, "program_level": "Bachelor",
                                  "nominal_duration_years": 4.0}])
    dim_student = pd.DataFrame([{"student_key": 1, "student_id": "S1", "gender": "Male", "birth_year": 2003,
                                  "home_province": "Nueva Ecija", "admission_type": "Freshman",
                                  "college_id": "CICT", "program_id": "CICT-BSIT-WEB",
                                  "_valid_from_semester_key": 1, "_valid_to_semester_key": None,
                                  "_is_current": True}])

    for name, df in [("dim_student", dim_student), ("dim_program", dim_program),
                      ("dim_college", dim_college), ("dim_semester", dim_semester),
                      ("dim_academic_year", build_dim_academic_year())]:
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        gold_storage.write_bytes(f"gold/{name}/data.parquet", buf.getvalue())

    empty_cols = {
        "enrollment": ["student_id", "academic_year", "semester_number", "college_id", "program_id",
                       "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"],
        "graduation": ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                       "years_to_complete"],
        "dropout": ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                    "dropout_reason", "semesters_completed_before_dropout"],
        "shifter": ["student_id", "academic_year", "semester_number", "from_program_id", "to_program_id"],
    }
    enrollment_df = pd.DataFrame([{
        "student_id": "S1", "academic_year": 2021, "semester_number": 1, "college_id": "CICT",
        "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED", "year_level": 1,
        "units_enrolled": 18, "is_new_enrollee": True,
    }])
    for name in ["graduation", "dropout", "shifter"]:
        empty_df = pd.DataFrame(columns=empty_cols[name])
        buf = io.BytesIO()
        empty_df.to_parquet(buf, engine="pyarrow", index=False)
        silver_storage.write_bytes(f"silver/{name}/data.parquet", buf.getvalue())
    buf = io.BytesIO()
    enrollment_df.to_parquet(buf, engine="pyarrow", index=False)
    silver_storage.write_bytes("silver/enrollment/data.parquet", buf.getvalue())

    counts1 = build_all_facts(silver_storage=silver_storage, gold_storage=gold_storage, meta_conn=meta_conn)
    counts2 = build_all_facts(silver_storage=silver_storage, gold_storage=gold_storage, meta_conn=meta_conn)

    assert counts1 == counts2  # identical row counts on re-run

    fact1 = pd.read_parquet(io.BytesIO(gold_storage.read_bytes("gold/fact_enrollment/data.parquet")))
    assert len(fact1) == 1  # re-run overwrote, did not append/duplicate
