"""
tests/unit/test_build_facts.py

Tests for pipelines/gold/build_facts.py. The centerpiece is proving the
AS-OF join actually resolves student_key correctly across a shift event --
a pre-shift enrollment record must point to the OLD dim_student row, a
post-shift record to the NEW one. Getting this wrong would silently make
every pre-shift enrollment record look like it was always in the
post-shift program, defeating the entire point of building SCD2 (Day 12).

Updated for the Task 23/24 Gold Modeling Fix: every fact now carries a
single `academic_period_key` (not the old semester_key + academic_year_key
pair), dim_student's SCD2 range columns are `_valid_from_period_key` /
`_valid_to_period_key`, and fact_enrollment carries `year_level_key` (FK to
the now-governed dim_year_level) instead of a raw year_level int. Also
reflects P0.4's 3-cohort / 6-period canonical horizon.
"""

import duckdb
import io
import pandas as pd
import pytest

from pipelines.gold.build_dimensions import build_dim_academic_period, academic_period_key_lookup
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
def dim_academic_period():
    return build_dim_academic_period()


@pytest.fixture
def dim_year_level():
    return pd.DataFrame([
        {"year_level_key": 1, "year_level": 1, "year_level_label": "Freshman"},
        {"year_level_key": 2, "year_level": 2, "year_level_label": "Sophomore"},
        {"year_level_key": 4, "year_level": 4, "year_level_label": "Senior"},
    ])


@pytest.fixture
def dims(dim_academic_period, dim_year_level):
    dim_college = pd.DataFrame([
        {"college_key": 1, "college_id": "CICT", "college_name": "College of ICT"},
        {"college_key": 2, "college_id": "COA", "college_name": "College of Architecture"},
    ])
    dim_program = pd.DataFrame([
        {"program_key": 1, "program_id": "CICT-BSIT-WEB", "program_name": "BSIT Web", "college_id": "CICT",
         "college_key": 1, "program_level": "Bachelor", "nominal_duration_years": 4.0},
        {"program_key": 2, "program_id": "COA-BSARCH", "program_name": "BS Architecture", "college_id": "COA",
         "college_key": 2, "program_level": "Bachelor", "nominal_duration_years": 5.0},
    ])
    # dim_student: one shifted student (S1, shifts at 2021-2) + one never-shifted (S2)
    period_key = academic_period_key_lookup(dim_academic_period)
    dim_student = pd.DataFrame([
        {"student_key": 1, "student_id": "S1", "gender_key": 2, "birth_year": 2003,
         "home_province": "Nueva Ecija", "admission_type": "Freshman",
         "college_key": 1, "program_key": 1,
         "_valid_from_period_key": period_key[(2021, 1)], "_valid_to_period_key": period_key[(2021, 1)],
         "_is_current": False},
        {"student_key": 2, "student_id": "S1", "gender_key": 2, "birth_year": 2003,
         "home_province": "Nueva Ecija", "admission_type": "Freshman",
         "college_key": 2, "program_key": 2,
         "_valid_from_period_key": period_key[(2021, 2)], "_valid_to_period_key": None,
         "_is_current": True},
        {"student_key": 3, "student_id": "S2", "gender_key": 1, "birth_year": 2003,
         "home_province": "Bulacan", "admission_type": "Freshman",
         "college_key": 1, "program_key": 1,
         "_valid_from_period_key": period_key[(2021, 1)], "_valid_to_period_key": None,
         "_is_current": True},
    ])
    return {"dim_student": dim_student, "dim_program": dim_program, "dim_college": dim_college,
            "dim_academic_period": dim_academic_period, "dim_year_level": dim_year_level}


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
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_academic_period"], conn)
    conn.close()
    assert result.iloc[0]["student_key"] == 1  # the OLD (pre-shift) row


def test_as_of_join_resolves_post_shift_record_to_new_student_key(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 2,  # AFTER the shift
         "college_id": "COA", "program_id": "COA-BSARCH", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_academic_period"], conn)
    conn.close()
    assert result.iloc[0]["student_key"] == 2  # the NEW (post-shift) row


def test_as_of_join_resolves_never_shifted_student_consistently(dims):
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S2", "academic_year": 2021, "semester_number": 1,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 1, "units_enrolled": 18, "is_new_enrollee": True},
        {"student_id": "S2", "academic_year": 2023, "semester_number": 2,  # last period of the 6-period horizon
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 4, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    result = resolve_student_key_as_of(enrollment, dims["dim_student"], dims["dim_academic_period"], conn)
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
    assert set(fact.columns) == {"student_key", "program_key", "college_key", "academic_period_key",
                                   "enrollment_status", "year_level_key",
                                   "units_enrolled", "is_new_enrollee"}


def test_build_fact_enrollment_raises_on_year_level_outside_governed_domain(dims):
    """dim_year_level's governed domain in this fixture is {1, 2, 4} -- a
    year_level of 3 (Junior) is deliberately absent to prove
    build_fact_enrollment fails loudly rather than silently dropping the row."""
    conn = duckdb.connect(":memory:")
    enrollment = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1,
         "college_id": "CICT", "program_id": "CICT-BSIT-WEB", "enrollment_status": "ENROLLED",
         "year_level": 3, "units_enrolled": 18, "is_new_enrollee": True},
    ])
    with pytest.raises(ValueError):
        build_fact_enrollment(enrollment, dims, conn)
    conn.close()


def test_build_fact_graduation_reconciles_row_count(dims):
    conn = duckdb.connect(":memory:")
    graduation = pd.DataFrame([
        {"student_id": "S2", "academic_year": 2023, "semester_number": 2,
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
    period_key = academic_period_key_lookup(dims["dim_academic_period"])
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1,
         "academic_period_key": period_key[(2023, 2)],
         "enrollment_status": "GRADUATED", "year_level_key": 4, "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_academic_period"])
    assert len(fact_retention) == 0  # graduated rows never appear in fact_retention


def test_fact_retention_excludes_final_observed_semester(dims):
    max_key = dims["dim_academic_period"]["academic_period_key"].max()
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "academic_period_key": max_key,
         "enrollment_status": "ENROLLED", "year_level_key": 4,
         "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_academic_period"])
    assert len(fact_retention) == 0  # 2023-2 has no "next semester" to check


def test_fact_retention_marks_continuing_student_as_retained(dims):
    keys = list(dims["dim_academic_period"]["academic_period_key"])
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "academic_period_key": keys[0],
         "enrollment_status": "ENROLLED", "year_level_key": 1,
         "units_enrolled": 18, "is_new_enrollee": True},
        {"student_key": 3, "program_key": 1, "college_key": 1, "academic_period_key": keys[1],
         "enrollment_status": "ENROLLED", "year_level_key": 1,
         "units_enrolled": 18, "is_new_enrollee": False},
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_academic_period"])
    # Both rows are eligible (neither GRADUATED nor the final observed semester):
    # keys[0] has a following ENROLLED record at keys[1] -> retained.
    # keys[1] has no following record at all (nothing at keys[2]) -> not retained.
    assert len(fact_retention) == 2
    row_at_keys0 = fact_retention[fact_retention["academic_period_key"] == keys[0]].iloc[0]
    row_at_keys1 = fact_retention[fact_retention["academic_period_key"] == keys[1]].iloc[0]
    assert row_at_keys0["is_retained"] == 1
    assert row_at_keys1["is_retained"] == 0


def test_fact_retention_marks_dropped_student_as_not_retained(dims):
    keys = list(dims["dim_academic_period"]["academic_period_key"])
    fact_enrollment = pd.DataFrame([
        {"student_key": 3, "program_key": 1, "college_key": 1, "academic_period_key": keys[0],
         "enrollment_status": "ENROLLED", "year_level_key": 1,
         "units_enrolled": 18, "is_new_enrollee": True},
        # no record at all in the next semester -- this student dropped
    ])
    fact_retention = build_fact_retention(fact_enrollment, dims["dim_academic_period"])
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

    dim_academic_period = build_dim_academic_period()
    dim_college = pd.DataFrame([{"college_key": 1, "college_id": "CICT", "college_name": "College of ICT"}])
    dim_program = pd.DataFrame([{"program_key": 1, "program_id": "CICT-BSIT-WEB", "program_name": "BSIT Web",
                                  "college_id": "CICT", "college_key": 1, "program_level": "Bachelor",
                                  "nominal_duration_years": 4.0}])
    dim_year_level = pd.DataFrame([{"year_level_key": 1, "year_level": 1, "year_level_label": "Freshman"}])
    period_key = academic_period_key_lookup(dim_academic_period)
    dim_student = pd.DataFrame([{"student_key": 1, "student_id": "S1", "gender_key": 2, "birth_year": 2003,
                                  "home_province": "Nueva Ecija", "admission_type": "Freshman",
                                  "college_key": 1, "program_key": 1,
                                  "_valid_from_period_key": period_key[(2021, 1)], "_valid_to_period_key": None,
                                  "_is_current": True}])

    for name, df in [("dim_student", dim_student), ("dim_program", dim_program),
                      ("dim_college", dim_college), ("dim_academic_period", dim_academic_period),
                      ("dim_year_level", dim_year_level)]:
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