"""
tests/unit/test_build_kpi.py

Tests for pipelines/gold/build_kpi.py: the weighted Success Rate formula
against docs/09_Data_Science.md's own worked example, weight-sum
validation, and the full aggregation against small fixtures.
"""

import duckdb
import pandas as pd
import pytest

from pipelines.gold.build_kpi import (
    WEIGHTS,
    build_fact_institution_kpi,
    compute_program_completion_momentum,
    compute_success_rate,
)


# ---------------------------------------------------------------------------
# Weight validation -- Day 14's validation checklist item 1
# ---------------------------------------------------------------------------

def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_success_rate -- against the doc's own worked example
# ---------------------------------------------------------------------------

def test_matches_documented_worked_example():
    """docs/09_Data_Science.md Section 4's worked example:
    R=0.88, G=0.22, D=0.06, Sh=0.97, E=0.95, P=0.80 -> 69.0"""
    result = compute_success_rate(
        retention_rate=0.88, graduation_rate=0.22, dropout_rate=0.06,
        shifter_stability=0.97, enrollment_stability=0.95, program_completion_momentum=0.80,
    )
    assert result == 69.0


def test_perfect_scores_yield_100():
    result = compute_success_rate(
        retention_rate=1.0, graduation_rate=1.0, dropout_rate=0.0,
        shifter_stability=1.0, enrollment_stability=1.0, program_completion_momentum=1.0,
    )
    assert result == 100.0


def test_worst_scores_yield_0():
    result = compute_success_rate(
        retention_rate=0.0, graduation_rate=0.0, dropout_rate=1.0,
        shifter_stability=0.0, enrollment_stability=0.0, program_completion_momentum=0.0,
    )
    assert result == 0.0


def test_higher_dropout_rate_lowers_score_all_else_equal():
    low_dropout = compute_success_rate(0.8, 0.3, 0.05, 0.9, 0.9, 0.7)
    high_dropout = compute_success_rate(0.8, 0.3, 0.30, 0.9, 0.9, 0.7)
    assert high_dropout < low_dropout


# ---------------------------------------------------------------------------
# compute_program_completion_momentum
# ---------------------------------------------------------------------------

def test_momentum_counts_advancing_students():
    fact_enrollment = pd.DataFrame([
        {"student_key": 1, "college_key": 1, "semester_key": 1, "year_level": 1},
        {"student_key": 1, "college_key": 1, "semester_key": 2, "year_level": 2},  # advanced
        {"student_key": 2, "college_key": 1, "semester_key": 1, "year_level": 1},
        {"student_key": 2, "college_key": 1, "semester_key": 2, "year_level": 1},  # stalled, did not advance
    ])
    conn = duckdb.connect(":memory:")
    result = compute_program_completion_momentum(fact_enrollment, conn)
    conn.close()
    row = result[result["semester_key"] == 2].iloc[0]
    assert row["momentum"] == 0.5  # 1 of 2 continuing students advanced


def test_momentum_excludes_new_entrants_with_no_prior_record():
    fact_enrollment = pd.DataFrame([
        {"student_key": 1, "college_key": 1, "semester_key": 1, "year_level": 1},
        {"student_key": 1, "college_key": 1, "semester_key": 2, "year_level": 2},
        {"student_key": 2, "college_key": 1, "semester_key": 2, "year_level": 1},  # brand new, no semester-1 record
    ])
    conn = duckdb.connect(":memory:")
    result = compute_program_completion_momentum(fact_enrollment, conn)
    conn.close()
    row = result[result["semester_key"] == 2].iloc[0]
    assert row["momentum"] == 1.0  # only student 1 is counted; student 2 excluded, not penalized


# ---------------------------------------------------------------------------
# build_fact_institution_kpi -- full aggregation against small fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kpi_fixtures():
    dim_program = pd.DataFrame([
        {"program_key": 1, "college_key": 1, "nominal_duration_years": 4.0},
    ])
    fact_enrollment = pd.DataFrame([
        {"student_key": 1, "program_key": 1, "college_key": 1, "semester_key": 1,
         "enrollment_status": "ENROLLED", "year_level": 4},
        {"student_key": 2, "program_key": 1, "college_key": 1, "semester_key": 1,
         "enrollment_status": "ENROLLED", "year_level": 1},
        {"student_key": 1, "program_key": 1, "college_key": 1, "semester_key": 2,
         "enrollment_status": "GRADUATED", "year_level": 4},
        {"student_key": 2, "program_key": 1, "college_key": 1, "semester_key": 2,
         "enrollment_status": "ENROLLED", "year_level": 2},
    ])
    fact_graduation = pd.DataFrame([
        {"student_key": 1, "college_key": 1, "semester_key": 2},
    ])
    fact_dropout = pd.DataFrame(columns=["student_key", "college_key", "semester_key"])
    fact_shifter = pd.DataFrame(columns=["student_key", "from_program_key", "to_program_key", "semester_key"])
    fact_retention = pd.DataFrame([
        {"student_key": 2, "program_key": 1, "college_key": 1, "semester_key": 1, "is_retained": 1},
    ])
    return fact_enrollment, fact_graduation, fact_dropout, fact_shifter, fact_retention, dim_program


def test_build_kpi_produces_one_row_per_college_semester(kpi_fixtures):
    conn = duckdb.connect(":memory:")
    kpi = build_fact_institution_kpi(*kpi_fixtures, conn)
    conn.close()
    assert set(zip(kpi["college_key"], kpi["semester_key"])) == {(1, 1), (1, 2)}


def test_build_kpi_graduation_rate_uses_eligible_denominator(kpi_fixtures):
    conn = duckdb.connect(":memory:")
    kpi = build_fact_institution_kpi(*kpi_fixtures, conn)
    conn.close()
    row = kpi[kpi["semester_key"] == 2].iloc[0]
    # 1 graduate, eligible pool at semester 2 = students with year_level >= 4:
    # student 1 has year_level=4 (eligible), student 2 has year_level=2 (not eligible) -> eligible=1
    assert row["graduation_count"] == 1
    assert row["graduation_rate"] == 1.0  # 1 graduate / 1 eligible


def test_build_kpi_every_row_has_success_rate_between_0_and_100(kpi_fixtures):
    conn = duckdb.connect(":memory:")
    kpi = build_fact_institution_kpi(*kpi_fixtures, conn)
    conn.close()
    assert (kpi["success_rate"] >= 0).all()
    assert (kpi["success_rate"] <= 100).all()


def test_build_kpi_shifter_events_attributed_to_from_college():
    """A shift event must count against the FROM college's shifter_stability,
    not the TO college -- it's the FROM college's population being depleted."""
    dim_program = pd.DataFrame([
        {"program_key": 1, "college_key": 1, "nominal_duration_years": 4.0},
        {"program_key": 2, "college_key": 2, "nominal_duration_years": 4.0},
    ])
    fact_enrollment = pd.DataFrame([
        {"student_key": 1, "program_key": 2, "college_key": 2, "semester_key": 1,
         "enrollment_status": "ENROLLED", "year_level": 1},
    ])
    fact_shifter = pd.DataFrame([{"student_key": 1, "from_program_key": 1, "to_program_key": 2, "semester_key": 1}])
    fact_graduation = pd.DataFrame(columns=["student_key", "college_key", "semester_key"])
    fact_dropout = pd.DataFrame(columns=["student_key", "college_key", "semester_key"])
    fact_retention = pd.DataFrame(columns=["student_key", "program_key", "college_key", "semester_key", "is_retained"])

    conn = duckdb.connect(":memory:")
    kpi = build_fact_institution_kpi(
        fact_enrollment, fact_graduation, fact_dropout, fact_shifter, fact_retention, dim_program, conn
    )
    conn.close()

    # The shifter event's college_key must resolve via from_program_key (college_key=1),
    # NOT to_program_key (college_key=2) -- even though fact_enrollment only has a
    # row for college_key=2.
    college_1_row = kpi[kpi["college_key"] == 1]
    assert len(college_1_row) == 1
    assert college_1_row.iloc[0]["shifter_count"] == 1
