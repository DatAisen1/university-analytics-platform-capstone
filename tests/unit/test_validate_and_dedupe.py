"""
tests/unit/test_validate_and_dedupe.py

Tests for pipelines/silver/validate_and_dedupe.py. Per Day 11's testing
checklist ("unit + integration tests with injected bad rows"), these
deliberately construct violations the real (correct-by-construction)
dataset never produces -- proving the quarantine mechanism actually works,
since the real data alone can't prove that (it never triggers it).
"""

from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest

from pipelines.common.storage import LocalFileStorage
from pipelines.silver.validate_and_dedupe import (
    check_dropout_consistency,
    check_known_status,
    check_semester_within_cohort_range,
    dedupe_enrollment,
    process_enrollment,
)
from pipelines.silver.progression_validation import check_year_level_progression


def _ts(offset_seconds: int = 0) -> datetime:
    from datetime import timedelta
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


# ---------------------------------------------------------------------------
# dedupe_enrollment
# ---------------------------------------------------------------------------

def test_dedupe_keeps_one_row_per_natural_key():
    df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "units_enrolled": 18,
         "_ingested_at": _ts(0)},
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "units_enrolled": 18,
         "_ingested_at": _ts(0)},  # exact duplicate, same timestamp
    ])
    conn = duckdb.connect(":memory:")
    result, dropped = dedupe_enrollment(df, conn)
    conn.close()
    assert len(result) == 1
    assert dropped == 1


def test_dedupe_keeps_latest_ingested_at_for_late_correction():
    """The late-correction scenario: two rows for the same natural key
    with DIFFERENT content (simulating a correction), differing
    _ingested_at -- the later one must win."""
    df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "units_enrolled": 15,
         "_ingested_at": _ts(0)},  # original, arrived first
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "units_enrolled": 21,
         "_ingested_at": _ts(100)},  # correction, arrived later -- should win
    ])
    conn = duckdb.connect(":memory:")
    result, dropped = dedupe_enrollment(df, conn)
    conn.close()
    assert len(result) == 1
    assert dropped == 1
    assert result.iloc[0]["units_enrolled"] == 21  # the later correction, not the original


def test_dedupe_preserves_distinct_natural_keys():
    df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "units_enrolled": 18, "_ingested_at": _ts(0)},
        {"student_id": "S1", "academic_year": 2021, "semester_number": 2, "units_enrolled": 18, "_ingested_at": _ts(0)},
        {"student_id": "S2", "academic_year": 2021, "semester_number": 1, "units_enrolled": 18, "_ingested_at": _ts(0)},
    ])
    conn = duckdb.connect(":memory:")
    result, dropped = dedupe_enrollment(df, conn)
    conn.close()
    assert len(result) == 3
    assert dropped == 0


# ---------------------------------------------------------------------------
# check_known_status
# ---------------------------------------------------------------------------

def test_check_known_status_quarantines_unknown_tagged_rows():
    df = pd.DataFrame([
        {"student_id": "S1", "enrollment_status": "ENROLLED"},
        {"student_id": "S2", "enrollment_status": "UNKNOWN:SUSPENDED"},
    ])
    valid, quarantined = check_known_status(df)
    assert len(valid) == 1
    assert len(quarantined) == 1
    assert quarantined.iloc[0]["student_id"] == "S2"
    assert "unrecognized enrollment_status" in quarantined.iloc[0]["_quarantine_reason"]


def test_check_known_status_passes_all_valid_rows():
    df = pd.DataFrame([
        {"student_id": "S1", "enrollment_status": "ENROLLED"},
        {"student_id": "S2", "enrollment_status": "GRADUATED"},
        {"student_id": "S3", "enrollment_status": "DROPPED"},
    ])
    valid, quarantined = check_known_status(df)
    assert len(valid) == 3
    assert len(quarantined) == 0


# ---------------------------------------------------------------------------
# check_semester_within_cohort_range
# ---------------------------------------------------------------------------

def test_check_semester_range_quarantines_record_before_cohort_entry():
    """Injected bad row: a 2022-cohort student with an enrollment record
    dated 2021-1 -- impossible, since they hadn't enrolled yet."""
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1},  # BAD: before entry
        {"student_id": "S1", "academic_year": 2022, "semester_number": 1},  # OK: their actual entry
    ])
    student_df = pd.DataFrame([{"student_id": "S1", "cohort_academic_year": 2022}])

    valid, quarantined = check_semester_within_cohort_range(enrollment_df, student_df)
    assert len(valid) == 1
    assert len(quarantined) == 1
    assert quarantined.iloc[0]["academic_year"] == 2021


def test_check_semester_range_quarantines_unknown_student():
    enrollment_df = pd.DataFrame([{"student_id": "GHOST", "academic_year": 2021, "semester_number": 1}])
    student_df = pd.DataFrame([{"student_id": "S1", "cohort_academic_year": 2021}])

    valid, quarantined = check_semester_within_cohort_range(enrollment_df, student_df)
    assert len(valid) == 0
    assert len(quarantined) == 1


def test_check_semester_range_accepts_valid_range():
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1},
        {"student_id": "S1", "academic_year": 2024, "semester_number": 2},  # last valid semester
    ])
    student_df = pd.DataFrame([{"student_id": "S1", "cohort_academic_year": 2021}])

    valid, quarantined = check_semester_within_cohort_range(enrollment_df, student_df)
    assert len(valid) == 2
    assert len(quarantined) == 0


# ---------------------------------------------------------------------------
# check_dropout_consistency
# ---------------------------------------------------------------------------

def test_check_dropout_consistency_quarantines_dropped_status_without_dropout_event():
    """Injected bad row: enrollment says DROPPED but there's no matching
    dropout event -- an internal inconsistency a correct pipeline should
    never produce, but Silver should catch if it ever did."""
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "enrollment_status": "DROPPED"},
    ])
    dropout_df = pd.DataFrame(columns=["student_id", "academic_year", "semester_number"])  # empty -- no matching event

    valid, quarantined = check_dropout_consistency(enrollment_df, dropout_df)
    assert len(valid) == 0
    assert len(quarantined) == 1


def test_check_dropout_consistency_quarantines_enrolled_status_with_dropout_event():
    """The inverse inconsistency: a dropout event exists but the
    enrollment row says ENROLLED, not DROPPED."""
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED"},
    ])
    dropout_df = pd.DataFrame([{"student_id": "S1", "academic_year": 2021, "semester_number": 1}])

    valid, quarantined = check_dropout_consistency(enrollment_df, dropout_df)
    assert len(valid) == 0
    assert len(quarantined) == 1


def test_check_dropout_consistency_accepts_correctly_matched_rows():
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": 2021, "semester_number": 1, "enrollment_status": "DROPPED"},
        {"student_id": "S2", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED"},
    ])
    dropout_df = pd.DataFrame([{"student_id": "S1", "academic_year": 2021, "semester_number": 1}])

    valid, quarantined = check_dropout_consistency(enrollment_df, dropout_df)
    assert len(valid) == 2
    assert len(quarantined) == 0


def test_check_year_level_progression_quarantines_impossible_transition():
    # The two rows must be CONSECUTIVE observed semesters (period_index
    # difference == 1) for the impossible-transition rule to apply at all
    # -- see progression_validation.py's module docstring: a transition
    # across a GAP (e.g. "1st Semester 2021-2022" -> "1st Semester
    # 2022-2023", which skips "2nd Semester 2021-2022" and is therefore 2
    # periods apart) is deliberately NOT checked. An earlier version of
    # this fixture used exactly that non-consecutive pair, which meant the
    # rule never fired regardless of the year_level jump.
    enrollment_df = pd.DataFrame([
        {"student_id": "S1", "academic_year": "2021-2022", "semester_name": "1st Semester",
         "year_level": 1, "enrollment_status": "ENROLLED"},
        {"student_id": "S1", "academic_year": "2021-2022", "semester_name": "2nd Semester",
         "year_level": 3, "enrollment_status": "ENROLLED"},
    ])

    valid, quarantined = check_year_level_progression(enrollment_df)
    assert len(valid) == 1
    assert len(quarantined) == 1
    assert quarantined.iloc[0]["year_level"] == 3


# ---------------------------------------------------------------------------
# process_enrollment -- end-to-end integration with injected bad rows
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_silver_with_bad_rows(tmp_path):
    """A small Silver enrollment dataset containing ONE of each kind of
    problem this stage should catch, plus clean rows that must survive.

    The clean-row count below (10 extra students, on top of S-CLEAN) is
    not arbitrary padding: process_enrollment enforces MAX_QUARANTINE_RATE
    = 0.25 in aggregate across all four checks (Task 47's quality gate --
    see validate_and_dedupe.py). With exactly 3 injected bad rows, the
    batch needs at least 13 total rows for 3/13 (~23%) to clear that
    tolerance; fewer rows would make the fixture itself trip the gate this
    stage is supposed to guard, independent of whether quarantining is
    working correctly. This is a fixture-realism fix, not a change to the
    gate's threshold.
    """
    storage = LocalFileStorage(tmp_path / "silver_store")

    extra_clean_ids = [f"S-CLEAN-{i}" for i in range(2, 12)]  # 10 more clean students

    student_df = pd.DataFrame(
        [
            {"student_id": "S-CLEAN", "cohort_academic_year": 2021},
            {"student_id": "S-DUP", "cohort_academic_year": 2021},
            {"student_id": "S-UNKNOWN", "cohort_academic_year": 2021},
            {"student_id": "S-RANGE", "cohort_academic_year": 2022},
            {"student_id": "S-INCONSISTENT", "cohort_academic_year": 2021},
        ]
        + [{"student_id": sid, "cohort_academic_year": 2021} for sid in extra_clean_ids]
    )

    dropout_df = pd.DataFrame([{"student_id": "S-CLEAN-DROPOUT", "academic_year": 2021, "semester_number": 1}])

    enrollment_df = pd.DataFrame(
        [
            {"student_id": "S-CLEAN", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED",
             "_ingested_at": _ts(0)},
            # duplicate -- should collapse to 1 via dedup
            {"student_id": "S-DUP", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED",
             "_ingested_at": _ts(0)},
            {"student_id": "S-DUP", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED",
             "_ingested_at": _ts(0)},
            # unknown status -- should quarantine
            {"student_id": "S-UNKNOWN", "academic_year": 2021, "semester_number": 1,
             "enrollment_status": "UNKNOWN:SUSPENDED", "_ingested_at": _ts(0)},
            # before cohort entry -- should quarantine
            {"student_id": "S-RANGE", "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED",
             "_ingested_at": _ts(0)},
            # DROPPED with no matching dropout event -- should quarantine
            {"student_id": "S-INCONSISTENT", "academic_year": 2021, "semester_number": 1,
             "enrollment_status": "DROPPED", "_ingested_at": _ts(0)},
        ]
        + [
            {"student_id": sid, "academic_year": 2021, "semester_number": 1, "enrollment_status": "ENROLLED",
             "_ingested_at": _ts(0)}
            for sid in extra_clean_ids
        ]
    )

    def _write(key, df):
        import io
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        storage.write_bytes(key, buf.getvalue())

    _write("silver/enrollment/data.parquet", enrollment_df)
    _write("silver/student/data.parquet", student_df)
    _write("silver/dropout/data.parquet", dropout_df)

    return storage


def test_process_enrollment_end_to_end_with_injected_bad_rows(fixture_silver_with_bad_rows, tmp_path):
    from pipelines.common.metadata import get_connection

    meta_conn = get_connection(tmp_path / "meta.duckdb")
    summary = process_enrollment(silver_storage=fixture_silver_with_bad_rows, meta_conn=meta_conn)

    assert summary["rows_in"] == 16  # 6 original + 10 padding clean rows (see fixture docstring)
    assert summary["duplicates_dropped"] == 1       # S-DUP's two identical rows -> 1
    assert summary["quarantined_unknown_status"] == 1   # S-UNKNOWN
    assert summary["quarantined_range"] == 1            # S-RANGE
    assert summary["quarantined_consistency"] == 1       # S-INCONSISTENT
    assert summary["total_quarantined"] == 3
    # 16 in - 1 duplicate - 3 quarantined = 12 remaining
    assert summary["rows_out"] == 12


def test_process_enrollment_writes_quarantine_table_with_reasons(fixture_silver_with_bad_rows, tmp_path):
    import io
    from pipelines.common.metadata import get_connection

    meta_conn = get_connection(tmp_path / "meta.duckdb")
    process_enrollment(silver_storage=fixture_silver_with_bad_rows, meta_conn=meta_conn)

    quarantine_df = pd.read_parquet(
        io.BytesIO(fixture_silver_with_bad_rows.read_bytes("silver_quarantine/enrollment/data.parquet"))
    )
    assert len(quarantine_df) == 3
    assert "_quarantine_reason" in quarantine_df.columns
    assert all(quarantine_df["_quarantine_reason"].notna())


def test_process_enrollment_final_silver_table_has_no_duplicate_natural_keys(fixture_silver_with_bad_rows, tmp_path):
    import io
    from pipelines.common.metadata import get_connection

    meta_conn = get_connection(tmp_path / "meta.duckdb")
    process_enrollment(silver_storage=fixture_silver_with_bad_rows, meta_conn=meta_conn)

    final_df = pd.read_parquet(
        io.BytesIO(fixture_silver_with_bad_rows.read_bytes("silver/enrollment/data.parquet"))
    )
    key_counts = final_df.groupby(["student_id", "academic_year", "semester_number"]).size()
    assert (key_counts > 1).sum() == 0