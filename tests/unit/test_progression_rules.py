"""
tests/unit/test_progression_rules.py

Unit tests for data_generator/rules/progression_rules.py -- the pure
probability functions behind the progression engine. Each test asserts a
specific, named claim the design makes (e.g. "dropout probability
decreases with year level") rather than just checking the function
returns *a* number.
"""

import numpy as np
import pytest

from data_generator.generators.generate_progression import load_progression_config, DEFAULT_PROGRESSION_CONFIG_PATH
from data_generator.rules.progression_rules import (
    dropout_probability,
    graduation_probability,
    max_year_level_cap,
    sample_dropout_reason,
    shift_probability,
    stall_probability,
)

CONFIG = load_progression_config(DEFAULT_PROGRESSION_CONFIG_PATH)


# ---------------------------------------------------------------------------
# dropout_probability
# ---------------------------------------------------------------------------

def test_dropout_probability_decreases_with_year_level():
    p1 = dropout_probability(year_level=1, risk_score=0.3, stall_count=0, config=CONFIG)
    p2 = dropout_probability(year_level=2, risk_score=0.3, stall_count=0, config=CONFIG)
    p3 = dropout_probability(year_level=3, risk_score=0.3, stall_count=0, config=CONFIG)
    assert p1 > p2 > p3


def test_dropout_probability_increases_with_risk_score():
    low = dropout_probability(year_level=1, risk_score=0.0, stall_count=0, config=CONFIG)
    high = dropout_probability(year_level=1, risk_score=1.0, stall_count=0, config=CONFIG)
    assert high > low


def test_dropout_probability_increases_with_stall_count():
    no_stall = dropout_probability(year_level=1, risk_score=0.5, stall_count=0, config=CONFIG)
    one_stall = dropout_probability(year_level=1, risk_score=0.5, stall_count=1, config=CONFIG)
    two_stalls = dropout_probability(year_level=1, risk_score=0.5, stall_count=2, config=CONFIG)
    assert no_stall < one_stall < two_stalls


def test_dropout_probability_is_capped():
    # Worst case: year 1, max risk, many stalls -- should hit the cap, not exceed it
    p = dropout_probability(year_level=1, risk_score=1.0, stall_count=10, config=CONFIG)
    assert p == CONFIG["dropout"]["max_probability"]


def test_dropout_probability_falls_back_for_unknown_year_level():
    """Year levels beyond the configured keys should reuse the highest
    defined key's rate rather than raising a KeyError."""
    p = dropout_probability(year_level=99, risk_score=0.5, stall_count=0, config=CONFIG)
    assert p > 0


# ---------------------------------------------------------------------------
# graduation_probability
# ---------------------------------------------------------------------------

def test_graduation_probability_increases_with_extra_semesters():
    just_eligible = graduation_probability(tenure_semesters=8, nominal_semesters=8, risk_score=0.3, config=CONFIG)
    two_extra = graduation_probability(tenure_semesters=10, nominal_semesters=8, risk_score=0.3, config=CONFIG)
    assert two_extra > just_eligible


def test_graduation_probability_decreases_with_risk_score():
    low_risk = graduation_probability(tenure_semesters=8, nominal_semesters=8, risk_score=0.0, config=CONFIG)
    high_risk = graduation_probability(tenure_semesters=8, nominal_semesters=8, risk_score=1.0, config=CONFIG)
    assert high_risk < low_risk


def test_graduation_probability_is_capped():
    p = graduation_probability(tenure_semesters=100, nominal_semesters=8, risk_score=0.0, config=CONFIG)
    assert p <= CONFIG["graduation"]["max_probability"]


def test_graduation_probability_never_negative():
    p = graduation_probability(tenure_semesters=8, nominal_semesters=8, risk_score=1.0, config=CONFIG)
    assert p >= 0.0


# ---------------------------------------------------------------------------
# stall_probability
# ---------------------------------------------------------------------------

def test_stall_probability_increases_with_risk_score():
    low = stall_probability(risk_score=0.0, config=CONFIG)
    high = stall_probability(risk_score=1.0, config=CONFIG)
    assert high > low


def test_stall_probability_bounded_by_one():
    p = stall_probability(risk_score=1.0, config=CONFIG)
    assert p <= 1.0


# ---------------------------------------------------------------------------
# shift_probability
# ---------------------------------------------------------------------------

def test_shift_probability_zero_after_year_two():
    assert shift_probability(year_level=3, config=CONFIG) == 0.0
    assert shift_probability(year_level=5, config=CONFIG) == 0.0


def test_shift_probability_positive_in_years_one_and_two():
    assert shift_probability(year_level=1, config=CONFIG) > 0
    assert shift_probability(year_level=2, config=CONFIG) > 0


def test_shift_probability_higher_in_year_one_than_year_two():
    assert shift_probability(year_level=1, config=CONFIG) > shift_probability(year_level=2, config=CONFIG)


# ---------------------------------------------------------------------------
# max_year_level_cap
# ---------------------------------------------------------------------------

def test_max_year_level_cap_adds_configured_allowance():
    cap = max_year_level_cap(nominal_duration_years=4, config=CONFIG)
    assert cap == 4 + CONFIG["stall"]["max_year_level_cap_extra_years"]


# ---------------------------------------------------------------------------
# sample_dropout_reason -- statistical shape test
# ---------------------------------------------------------------------------

def test_sample_dropout_reason_matches_configured_weights():
    rng = np.random.default_rng(7)
    n = 20_000
    draws = [sample_dropout_reason(rng, CONFIG) for _ in range(n)]
    observed_financial_share = draws.count("Financial") / n
    assert observed_financial_share == pytest.approx(
        CONFIG["dropout"]["reason_weights"]["Financial"], abs=0.02
    )


def test_sample_dropout_reason_only_returns_known_reasons():
    rng = np.random.default_rng(7)
    known_reasons = set(CONFIG["dropout"]["reason_weights"].keys())
    draws = {sample_dropout_reason(rng, CONFIG) for _ in range(200)}
    assert draws <= known_reasons
