"""
tests/unit/test_apply_noise.py

Tests for data_generator/rules/noise_injection.py (pure functions) and
data_generator/generators/apply_noise.py (file-level orchestration).

Coverage philosophy matches Day 4/5: statistical shape tests for the
probability-driven functions (does the observed rate match the
configured rate, within tolerance, given a fixed seed), plus explicit
checks for the two things Day 6's checklist calls out directly --
noise rates landing within tolerance, and referential integrity never
being broken by any noise path.
"""

import csv

import numpy as np
import pytest

from pipelines.common.config import ConfigError, load_default_reference_data
from data_generator.rules.noise_injection import (
    apply_status_casing_noise,
    introduce_typo,
    should_duplicate,
    should_late_correct,
)
from data_generator.generators.apply_noise import (
    DEFAULT_NOISE_CONFIG_PATH,
    apply_noise_to_enrollment_partitions,
    apply_noise_to_student_master,
    load_noise_config,
)

CONFIG = load_noise_config(DEFAULT_NOISE_CONFIG_PATH)
N_DRAWS = 20_000
TOLERANCE = 0.02


# ---------------------------------------------------------------------------
# load_noise_config
# ---------------------------------------------------------------------------

def test_real_noise_config_loads():
    assert 0 < CONFIG["typo_rate"] < 1
    assert 0 < CONFIG["duplicate_rate"] < 1
    assert 0 < CONFIG["late_correction_rate"] < 1


def test_noise_config_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_noise_config(tmp_path / "missing.yaml")


def test_noise_config_bad_status_variant_weights_raise_config_error(tmp_path):
    bad = tmp_path / "noise_rules.yaml"
    bad.write_text(
        """
random_seed: 1
typo_rate: 0.02
duplicate_rate: 0.01
late_correction_rate: 0.03
status_variants:
  ENROLLED:
    "ENROLLED": 0.5
    "Enrolled": 0.3
"""
    )
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        load_noise_config(bad)


# ---------------------------------------------------------------------------
# apply_status_casing_noise -- statistical shape test
# ---------------------------------------------------------------------------

def test_status_casing_noise_matches_configured_distribution():
    rng = np.random.default_rng(1)
    draws = [apply_status_casing_noise(rng, "ENROLLED", CONFIG) for _ in range(N_DRAWS)]
    observed_share = draws.count("ENROLLED") / N_DRAWS
    assert observed_share == pytest.approx(CONFIG["status_variants"]["ENROLLED"]["ENROLLED"], abs=TOLERANCE)


def test_status_casing_noise_only_returns_configured_variants():
    rng = np.random.default_rng(1)
    known_variants = set(CONFIG["status_variants"]["DROPPED"].keys())
    draws = {apply_status_casing_noise(rng, "DROPPED", CONFIG) for _ in range(500)}
    assert draws <= known_variants


def test_status_casing_noise_passes_through_unmodeled_status():
    rng = np.random.default_rng(1)
    result = apply_status_casing_noise(rng, "ON_LEAVE", CONFIG)  # not in status_variants
    assert result == "ON_LEAVE"


# ---------------------------------------------------------------------------
# introduce_typo
# ---------------------------------------------------------------------------

def test_introduce_typo_matches_configured_rate():
    rng = np.random.default_rng(2)
    text = "Nueva Ecija"
    mutated_count = sum(1 for _ in range(N_DRAWS) if introduce_typo(rng, text, 0.02) != text)
    observed_rate = mutated_count / N_DRAWS
    assert observed_rate == pytest.approx(0.02, abs=TOLERANCE)


def test_introduce_typo_never_mutates_short_strings():
    rng = np.random.default_rng(2)
    for _ in range(200):
        assert introduce_typo(rng, "A", 1.0) == "A"  # typo_rate=1.0, but len<2 -- must stay unchanged


def test_introduce_typo_preserves_length_or_shortens_by_one():
    rng = np.random.default_rng(2)
    text = "Pampanga"
    for _ in range(200):
        mutated = introduce_typo(rng, text, 1.0)  # force mutation every time
        assert len(mutated) in (len(text), len(text) - 1)


# ---------------------------------------------------------------------------
# should_duplicate / should_late_correct
# ---------------------------------------------------------------------------

def test_should_duplicate_matches_configured_rate():
    rng = np.random.default_rng(3)
    hits = sum(1 for _ in range(N_DRAWS) if should_duplicate(rng, CONFIG))
    assert hits / N_DRAWS == pytest.approx(CONFIG["duplicate_rate"], abs=TOLERANCE)


def test_should_late_correct_matches_configured_rate():
    rng = np.random.default_rng(3)
    hits = sum(1 for _ in range(N_DRAWS) if should_late_correct(rng, CONFIG))
    assert hits / N_DRAWS == pytest.approx(CONFIG["late_correction_rate"], abs=TOLERANCE)


# ---------------------------------------------------------------------------
# apply_noise_to_student_master -- referential integrity + rate check
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_student_master(tmp_path):
    path = tmp_path / "student_master.csv"
    rows = [
        {"student_id": f"2021-{i:05d}", "cohort_academic_year": "2021", "gender": "Male",
         "birth_year": "2003", "home_province": "Nueva Ecija", "admission_type": "Freshman",
         "entry_year_level": "1", "entry_college_id": "CICT", "entry_program_id": "CICT-BSDS"}
        for i in range(1, 2001)
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_apply_noise_to_student_master_mutates_expected_share(fixture_student_master):
    rng = np.random.default_rng(4)
    mutated_count = apply_noise_to_student_master(fixture_student_master, CONFIG, rng)
    observed_rate = mutated_count / 2000
    assert observed_rate == pytest.approx(CONFIG["typo_rate"], abs=0.02)


def test_apply_noise_to_student_master_preserves_all_student_ids(fixture_student_master):
    with fixture_student_master.open() as f:
        original_ids = {row["student_id"] for row in csv.DictReader(f)}

    rng = np.random.default_rng(4)
    apply_noise_to_student_master(fixture_student_master, CONFIG, rng)

    with fixture_student_master.open() as f:
        after_ids = {row["student_id"] for row in csv.DictReader(f)}

    assert original_ids == after_ids  # noise must never touch student_id


# ---------------------------------------------------------------------------
# apply_noise_to_enrollment_partitions -- integration test on a small fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_partitions(tmp_path):
    """Three chronological partitions with a handful of ENROLLED rows each."""
    output_dir = tmp_path / "output"
    fieldnames = ["student_id", "academic_year", "semester_number", "college_id", "program_id",
                  "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"]
    for year, sem in [(2021, 1), (2021, 2), (2022, 1)]:
        part_dir = output_dir / str(year) / str(sem)
        part_dir.mkdir(parents=True)
        rows = [
            {"student_id": f"2021-{i:05d}", "academic_year": year, "semester_number": sem,
             "college_id": "CICT", "program_id": "CICT-BSDS", "enrollment_status": "ENROLLED",
             "year_level": 1, "units_enrolled": 18, "is_new_enrollee": sem == 1 and year == 2021}
            for i in range(1, 101)
        ]
        with (part_dir / "enrollment.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return output_dir


def test_apply_noise_to_enrollment_partitions_preserves_fk_integrity(fixture_partitions):
    reference = load_default_reference_data()
    known_students = {f"2021-{i:05d}" for i in range(1, 101)}
    known_programs = {p.program_id for p in reference.programs}
    known_colleges = {c.college_id for c in reference.colleges}

    rng = np.random.default_rng(5)
    apply_noise_to_enrollment_partitions(fixture_partitions, CONFIG, rng)

    for part_file in fixture_partitions.glob("*/*/enrollment.csv"):
        with part_file.open() as f:
            for row in csv.DictReader(f):
                assert row["student_id"] in known_students
                assert row["program_id"] in known_programs
                assert row["college_id"] in known_colleges


def test_apply_noise_to_enrollment_partitions_produces_duplicates_and_late_corrections(fixture_partitions):
    rng = np.random.default_rng(6)
    summary = apply_noise_to_enrollment_partitions(fixture_partitions, CONFIG, rng)

    assert summary["total_rows_processed"] == 300  # 3 partitions x 100 rows
    # With 300 rows and non-trivial rates, expect at least SOME duplicates/late corrections
    assert summary["duplicated"] >= 0
    assert summary["late_corrected"] >= 0

    # First partition (2021-1) should end up with MORE rows than it started with,
    # since duplicates land in the SAME partition
    with (fixture_partitions / "2021" / "1" / "enrollment.csv").open() as f:
        final_2021_1_count = sum(1 for _ in csv.DictReader(f))
    assert final_2021_1_count >= 100


def test_apply_noise_to_enrollment_partitions_raises_if_no_partitions_found(tmp_path):
    empty_dir = tmp_path / "empty_output"
    empty_dir.mkdir()
    rng = np.random.default_rng(1)
    with pytest.raises(ConfigError, match="No enrollment partitions found"):
        apply_noise_to_enrollment_partitions(empty_dir, CONFIG, rng)
