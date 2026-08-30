"""
tests/unit/test_generate_progression.py

Tests for data_generator/generators/generate_progression.py: the
semester-index helpers, single-student simulation, and full-population
orchestration + reconciliation, run against a small synthetic fixture
population (not the full 7,800-student dataset -- fast and deterministic).
"""

import csv

import numpy as np
import pytest

from pipelines.common.config import load_default_reference_data
from data_generator.generators.generate_progression import (
    DEFAULT_PROGRESSION_CONFIG_PATH,
    entry_semester_index,
    generate_all_progression,
    load_progression_config,
    pick_shift_target_program,
    semester_index_to_label,
    simulate_student,
)

CONFIG = load_progression_config(DEFAULT_PROGRESSION_CONFIG_PATH)
REFERENCE = load_default_reference_data()


# ---------------------------------------------------------------------------
# Semester index helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("idx,expected", [
    (0, ("2021-2022", "1st Semester")),
    (1, ("2021-2022", "2nd Semester")),
    (2, ("2022-2023", "1st Semester")),
    (7, ("2024-2025", "2nd Semester")),
])
def test_semester_index_to_label(idx, expected):
    assert semester_index_to_label(idx) == expected


@pytest.mark.parametrize("cohort_year,expected_idx", [
    (2021, 0), (2022, 2), (2023, 4), (2024, 6),
])
def test_entry_semester_index(cohort_year, expected_idx):
    assert entry_semester_index(cohort_year) == expected_idx


# ---------------------------------------------------------------------------
# pick_shift_target_program
# ---------------------------------------------------------------------------

def test_pick_shift_target_program_never_returns_same_program():
    rng = np.random.default_rng(1)
    current = REFERENCE.as_program_lookup()["CICT-BSIT-NET"]
    for _ in range(200):
        new_program = pick_shift_target_program(rng, REFERENCE, current, CONFIG)
        assert new_program.program_id != current.program_id


def test_pick_shift_target_program_mostly_same_college():
    rng = np.random.default_rng(1)
    current = REFERENCE.as_program_lookup()["CICT-BSIT-NET"]
    n = 2000
    same_college_count = sum(
        1 for _ in range(n)
        if pick_shift_target_program(rng, REFERENCE, current, CONFIG).college_id == current.college_id
    )
    observed_share = same_college_count / n
    assert observed_share == pytest.approx(CONFIG["shifter"]["same_college_weight"], abs=0.03)


# ---------------------------------------------------------------------------
# simulate_student -- single-student scenarios with controlled inputs
# ---------------------------------------------------------------------------

def test_simulate_student_always_terminates_by_max_index():
    """Regardless of outcome, no enrollment record should reference a
    semester beyond max_semester_index (2024-2)."""
    rng = np.random.default_rng(5)
    program = REFERENCE.as_program_lookup()["CICT-BSDS"]
    result = simulate_student(
        "TEST-0001", cohort_year=2021, entry_year_level=1, entry_program=program,
        risk_score=0.5, reference=REFERENCE, rng=rng, config=CONFIG,
    )
    for rec in result["enrollment_records"]:
        academic_year = int(rec["academic_year"].split("-")[0])
        semester_number = 1 if rec["semester_name"] == "1st Semester" else 2
        idx = (academic_year - 2021) * 2 + (semester_number - 1)
        assert idx <= CONFIG["max_semester_index"]


def test_simulate_student_produces_exactly_one_terminal_outcome():
    """A student either graduates, drops, or is still active -- never both
    graduated AND dropped."""
    rng = np.random.default_rng(9)
    program = REFERENCE.as_program_lookup()["COA-CERT-DRAFT"]  # 1-year program: fast to reach a terminal outcome
    for i in range(50):
        result = simulate_student(
            f"TEST-{i:04d}", cohort_year=2021, entry_year_level=1, entry_program=program,
            risk_score=0.5, reference=REFERENCE, rng=rng, config=CONFIG,
        )
        has_grad = result["graduation_record"] is not None
        has_drop = result["dropout_record"] is not None
        assert not (has_grad and has_drop), f"student {i} both graduated and dropped"
        assert result["final_status"] in {"ACTIVE", "GRADUATED", "DROPPED"}


def test_high_risk_students_drop_out_more_often_than_low_risk_students():
    """Statistical claim: over many simulated students, a population with
    risk_score=0.9 should show a materially higher dropout rate than a
    population with risk_score=0.05, holding everything else constant."""
    program = REFERENCE.as_program_lookup()["CMBT-BSENTREP"]
    n = 300

    def dropout_rate(risk_score, seed):
        rng = np.random.default_rng(seed)
        outcomes = [
            simulate_student(f"S-{i}", 2021, 1, program, risk_score, REFERENCE, rng, CONFIG)["final_status"]
            for i in range(n)
        ]
        return outcomes.count("DROPPED") / n

    low_risk_rate = dropout_rate(0.05, seed=11)
    high_risk_rate = dropout_rate(0.9, seed=11)
    assert high_risk_rate > low_risk_rate


def test_low_risk_short_program_student_usually_graduates():
    """A low-risk student in a 1-year certificate program, simulated across
    the full 4-year window, should graduate far more often than not --
    they get many eligibility windows (nominal 2 semesters vs. up to 8
    observed) and low risk keeps both dropout and stall rates down."""
    program = REFERENCE.as_program_lookup()["IPE-CERT-PE"]  # nominal_duration_years = 1
    n = 300
    rng = np.random.default_rng(21)
    outcomes = [
        simulate_student(f"S-{i}", 2021, 1, program, risk_score=0.05, reference=REFERENCE, rng=rng, config=CONFIG)["final_status"]
        for i in range(n)
    ]
    graduated_share = outcomes.count("GRADUATED") / n
    assert graduated_share > 0.7


def test_five_year_program_can_graduate_within_extended_observed_window():
    """P0 Dataset Extension flips this test's old premise on purpose.

    Previously (max_semester_index=5, i.e. the 2021-2023/6-period window):
    a 5-year program (10 nominal semesters) entered in the 2021 cohort
    could accumulate at most 6 semesters of tenure by 2023-2 -- never
    reaching eligibility, so this test asserted `graduations == 0` and
    existed specifically to keep that limitation honest.

    Now (max_semester_index=9, i.e. the 2021-2025/10-period window): the
    2021 cohort reaches exactly 10 semesters of tenure by 2025-2 -- the
    minimum a 5-year program needs -- so on-time entrants CAN graduate for
    the first time. This is not incidental to the extension; it's one of
    the modeling gaps the extension was explicitly meant to close (see
    generate_progression.py's module docstring and docs/10_Forecasting.md
    §1's dataset-horizon note).

    This test now asserts the opposite of what it used to: if
    generate_progression.py ever regresses back to making this
    structurally impossible, this test should fail and force that
    regression to be noticed, the same protective role it played before
    just pointed the other direction."""
    program = REFERENCE.as_program_lookup()["COE-BSCE"]  # nominal_duration_years = 5
    rng = np.random.default_rng(33)
    graduations = 0
    for i in range(200):
        result = simulate_student(
            f"S-{i}", cohort_year=2021, entry_year_level=1, entry_program=program,
            risk_score=0.0, reference=REFERENCE, rng=rng, config=CONFIG,  # risk=0 to maximize survival
        )
        if result["final_status"] == "GRADUATED":
            graduations += 1
    # Not a tight bound on the exact rate (that's a property of the
    # progression-probability config, not this test's concern) -- just
    # confirming graduation is structurally reachable at all now, which
    # is the thing the extension changed. Empirically ~46/200 at risk=0
    # with this seed; a low, non-zero floor guards against a future
    # regression re-introducing the impossibility without pinning an
    # exact rate that has no principled "correct" value.
    assert graduations > 0


# ---------------------------------------------------------------------------
# generate_all_progression -- end-to-end against a small fixture population
# ---------------------------------------------------------------------------

@pytest.fixture
def small_fixture_population(tmp_path):
    """A tiny, hand-built student_master.csv + risk profile pair, entirely
    separate from the real 7,800-student dataset -- keeps this test fast
    and independent of the Day 4 generator's output."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    internal_dir = output_dir / "_internal"
    internal_dir.mkdir()

    students = [
        {"student_id": "2021-00001", "cohort_academic_year": "2021", "gender": "Male", "birth_year": "2003",
         "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": "1",
         "entry_college_id": "IPE", "entry_program_id": "IPE-CERT-PE"},
        {"student_id": "2021-00002", "cohort_academic_year": "2021", "gender": "Female", "birth_year": "2003",
         "home_province": "Nueva Ecija", "admission_type": "Freshman", "entry_year_level": "1",
         "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS"},
        {"student_id": "2022-00001", "cohort_academic_year": "2022", "gender": "Male", "birth_year": "2004",
         "home_province": "Bulacan", "admission_type": "Freshman", "entry_year_level": "1",
         "entry_college_id": "COED", "entry_program_id": "COED-BEED"},
    ]
    fieldnames = list(students[0].keys())
    with (output_dir / "student_master.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(students)

    with (internal_dir / "student_latent_profiles.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["student_id", "risk_score"])
        writer.writeheader()
        for s in students:
            writer.writerow({"student_id": s["student_id"], "risk_score": "0.2"})

    return output_dir


def test_generate_all_progression_reconciles_cohort_totals(small_fixture_population):
    summary = generate_all_progression(
        student_master_path=small_fixture_population / "student_master.csv",
        risk_profiles_path=small_fixture_population / "_internal" / "student_latent_profiles.csv",
        output_dir=small_fixture_population,
    )
    assert summary["total_students"] == 3
    assert summary["cohort_totals_reconciled"] is True
    # every student accounted for exactly once across ACTIVE/GRADUATED/DROPPED
    total_outcomes = sum(sum(c.values()) for c in summary["outcome_by_cohort"].values())
    assert total_outcomes == 3


def test_generate_all_progression_writes_partitioned_files(small_fixture_population):
    generate_all_progression(
        student_master_path=small_fixture_population / "student_master.csv",
        risk_profiles_path=small_fixture_population / "_internal" / "student_latent_profiles.csv",
        output_dir=small_fixture_population,
    )
    # At minimum, the entry semester (2021, 1) must have an enrollment partition
    assert (small_fixture_population / "2021-2022" / "1st Semester" / "enrollment.csv").exists()


def test_generate_all_progression_no_enrollment_record_missing_required_fields(small_fixture_population):
    generate_all_progression(
        student_master_path=small_fixture_population / "student_master.csv",
        risk_profiles_path=small_fixture_population / "_internal" / "student_latent_profiles.csv",
        output_dir=small_fixture_population,
    )
    enrollment_file = small_fixture_population / "2021-2022" / "1st Semester" / "enrollment.csv"
    with enrollment_file.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    for row in rows:
        assert row["student_id"]
        assert row["enrollment_status"] in {"ENROLLED", "GRADUATED", "DROPPED"}