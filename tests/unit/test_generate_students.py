"""
tests/unit/test_generate_students.py

Unit tests for data_generator/generators/generate_students.py.

These target the generator's PURE probability/sampling functions in
isolation -- given a fixed seed, does each function draw from the
distribution it claims to? This is what "unit test the generator's
probability functions" (Day 4 testing checklist) means concretely: not
"run the whole generator and eyeball a CSV," but "assert a large sample
of draws lands close to the configured weights, deterministically."
"""


import numpy as np
import pytest

from pipelines.common.config import ConfigError, load_default_reference_data
from data_generator.generators.generate_students import (
    DEFAULT_VOLUMES_PATH,
    generate_all_students,
    generate_cohort,
    load_volumes_config,
    make_student_id,
    normalize_weights,
    sample_age_offset,
    sample_entry_year_level,
    sample_risk_score,
    validate_college_weights_match_reference,
    validate_weights_sum_to_one,
    weighted_choice,
)

SEED = 42
N_DRAWS = 20_000
TOLERANCE = 0.02  # 2 percentage points -- generous enough to not be flaky, tight enough to catch real bugs


# ---------------------------------------------------------------------------
# normalize_weights / validate_weights_sum_to_one
# ---------------------------------------------------------------------------

def test_normalize_weights_sums_to_one():
    weights = {"a": 2.0, "b": 2.0, "c": 6.0}
    normalized = normalize_weights(weights)
    assert abs(sum(normalized.values()) - 1.0) < 1e-9
    assert normalized["c"] == pytest.approx(0.6)


def test_validate_weights_sum_to_one_accepts_valid_weights():
    validate_weights_sum_to_one({"a": 0.5, "b": 0.5}, label="test")  # should not raise


def test_validate_weights_sum_to_one_rejects_bad_total():
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        validate_weights_sum_to_one({"a": 0.5, "b": 0.4}, label="test")


# ---------------------------------------------------------------------------
# weighted_choice -- statistical shape test
# ---------------------------------------------------------------------------

def test_weighted_choice_matches_configured_distribution():
    rng = np.random.default_rng(SEED)
    weights = {"Male": 0.48, "Female": 0.52}
    draws = [weighted_choice(rng, weights) for _ in range(N_DRAWS)]

    observed_female_share = draws.count("Female") / N_DRAWS
    assert observed_female_share == pytest.approx(0.52, abs=TOLERANCE)


def test_weighted_choice_is_deterministic_given_same_seed():
    weights = {"A": 0.3, "B": 0.3, "C": 0.4}
    draws_1 = [weighted_choice(np.random.default_rng(SEED), weights) for _ in range(50)]
    draws_2 = [weighted_choice(np.random.default_rng(SEED), weights) for _ in range(50)]
    assert draws_1 == draws_2


def test_weighted_choice_only_returns_known_keys():
    rng = np.random.default_rng(SEED)
    weights = {"X": 0.1, "Y": 0.9}
    draws = {weighted_choice(rng, weights) for _ in range(200)}
    assert draws <= {"X", "Y"}


# ---------------------------------------------------------------------------
# sample_age_offset
# ---------------------------------------------------------------------------

def test_sample_age_offset_matches_configured_distribution():
    rng = np.random.default_rng(SEED)
    weights = {0: 0.55, 1: 0.20, 2: 0.10, 3: 0.06, 4: 0.04, 5: 0.03, 6: 0.01, 7: 0.01}
    draws = [sample_age_offset(rng, weights) for _ in range(N_DRAWS)]

    observed_zero_share = draws.count(0) / N_DRAWS
    assert observed_zero_share == pytest.approx(0.55, abs=TOLERANCE)
    assert all(0 <= d <= 7 for d in draws)


# ---------------------------------------------------------------------------
# sample_risk_score
# ---------------------------------------------------------------------------

def test_sample_risk_score_stays_in_unit_interval():
    rng = np.random.default_rng(SEED)
    scores = [sample_risk_score(rng, alpha=2.0, beta=5.0) for _ in range(N_DRAWS)]
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_sample_risk_score_is_skewed_toward_low_risk():
    """alpha < beta should put the mean well below 0.5 -- most students
    should be low-risk, a minority high-risk."""
    rng = np.random.default_rng(SEED)
    scores = [sample_risk_score(rng, alpha=2.0, beta=5.0) for _ in range(N_DRAWS)]
    mean_score = sum(scores) / len(scores)
    # theoretical mean of Beta(2,5) = 2/(2+5) ≈ 0.2857
    assert mean_score == pytest.approx(2 / 7, abs=0.02)


# ---------------------------------------------------------------------------
# sample_entry_year_level
# ---------------------------------------------------------------------------

def test_freshmen_always_enter_at_year_level_one():
    rng = np.random.default_rng(SEED)
    levels = {sample_entry_year_level(rng, "Freshman") for _ in range(200)}
    assert levels == {1}


def test_transferees_enter_at_year_level_two_or_three():
    rng = np.random.default_rng(SEED)
    levels = [sample_entry_year_level(rng, "Transferee") for _ in range(2000)]
    assert set(levels) == {2, 3}
    # majority should be year 2 per the configured 0.8/0.2 split
    assert levels.count(2) / len(levels) == pytest.approx(0.8, abs=TOLERANCE)


# ---------------------------------------------------------------------------
# make_student_id
# ---------------------------------------------------------------------------

def test_make_student_id_format():
    assert make_student_id(2021, 1) == "2021-00001"
    assert make_student_id(2024, 12345) == "2024-12345"


def test_make_student_id_unique_across_cohorts_and_sequence():
    ids = {make_student_id(year, seq) for year in (2021, 2022) for seq in range(1, 101)}
    assert len(ids) == 200  # no collisions


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------

def test_real_volumes_config_loads():
    config = load_volumes_config(DEFAULT_VOLUMES_PATH)
    assert "cohort_sizes" in config
    assert sum(config["cohort_sizes"].values()) > 0


def test_volumes_config_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_volumes_config(tmp_path / "does_not_exist.yaml")


def test_volumes_config_bad_weights_raise_config_error(tmp_path):
    bad_config = tmp_path / "volumes.yaml"
    bad_config.write_text(
        """
random_seed: 1
cohort_sizes:
  2021: 10
college_weights:
  COA: 0.5
  COED: 0.6
admission_type_weights:
  Freshman: 1.0
gender_weights:
  Male: 1.0
home_province_weights:
  "Nueva Ecija": 1.0
age_offset_weights:
  0: 1.0
risk_profile:
  beta_alpha: 2.0
  beta_beta: 5.0
"""
    )
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        load_volumes_config(bad_config)


def test_college_weights_cross_validation_catches_unknown_college():
    reference = load_default_reference_data()
    with pytest.raises(ConfigError, match="unknown college_id"):
        validate_college_weights_match_reference({"NOPE": 1.0}, reference)


def test_college_weights_cross_validation_catches_missing_college():
    reference = load_default_reference_data()
    # Omit one real college_id -- e.g. drop COA
    incomplete = {c.college_id: 1 / 7 for c in reference.colleges if c.college_id != "COA"}
    with pytest.raises(ConfigError, match="missing weight"):
        validate_college_weights_match_reference(incomplete, reference)


def test_real_college_weights_pass_cross_validation():
    config = load_volumes_config(DEFAULT_VOLUMES_PATH)
    reference = load_default_reference_data()
    validate_college_weights_match_reference(config["college_weights"], reference)  # should not raise


# ---------------------------------------------------------------------------
# generate_cohort -- integration of the pure functions above
# ---------------------------------------------------------------------------

def test_generate_cohort_produces_requested_size_and_unique_ids():
    reference = load_default_reference_data()
    config = load_volumes_config(DEFAULT_VOLUMES_PATH)
    rng = np.random.default_rng(SEED)

    rows = generate_cohort(2021, 500, reference, config, rng)

    assert len(rows) == 500
    ids = [r["student_id"] for r in rows]
    assert len(set(ids)) == 500  # no duplicates within a single cohort call

    known_college_ids = {c.college_id for c in reference.colleges}
    known_program_ids = {p.program_id for p in reference.programs}
    for row in rows:
        assert row["entry_college_id"] in known_college_ids
        assert row["entry_program_id"] in known_program_ids
        # program must actually belong to the assigned college
        program = reference.as_program_lookup()[row["entry_program_id"]]
        assert program.college_id == row["entry_college_id"]
        assert 0.0 <= row["risk_score"] <= 1.0


# ---------------------------------------------------------------------------
# generate_all_students -- end-to-end, writes real files (Day 4 expected output)
# ---------------------------------------------------------------------------

def test_generate_all_students_end_to_end(tmp_path):
    output_dir = tmp_path / "output"
    summary = generate_all_students(output_dir=output_dir)

    student_master = output_dir / "student_master.csv"
    risk_profiles = output_dir / "_internal" / "student_latent_profiles.csv"

    assert student_master.exists()
    assert risk_profiles.exists()
    assert sum(summary.values()) > 0

    import csv
    with student_master.open() as f:
        rows = list(csv.DictReader(f))
    # risk_score must NOT leak into the public file
    assert "risk_score" not in rows[0].keys()

    with risk_profiles.open() as f:
        risk_rows = list(csv.DictReader(f))
    assert len(risk_rows) == len(rows)

    student_ids_master = {r["student_id"] for r in rows}
    student_ids_risk = {r["student_id"] for r in risk_rows}
    assert student_ids_master == student_ids_risk  # same population, both files
