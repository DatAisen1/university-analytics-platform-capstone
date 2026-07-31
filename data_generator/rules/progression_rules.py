"""
data_generator/rules/progression_rules.py

Pure probability functions driving the student progression engine
(data_generator/generators/generate_progression.py). Kept separate from
the simulation loop for the same reason Day 4's sampling functions were
kept separate from generate_cohort: each function takes plain values in
and returns a plain value out, so it can be unit-tested in isolation --
"does dropout probability actually increase with risk_score and stall
count, and decrease with year level" is a testable claim about a pure
function, not something you can cleanly assert about a stateful loop.
"""

from __future__ import annotations

from typing import Dict

import numpy as np


def _interpolate(min_v: float, max_v: float, t: float) -> float:
    """Linear interpolation between min_v and max_v as t goes 0 -> 1.
    t is clamped to [0, 1] so an out-of-range risk_score can't produce a
    multiplier outside the intended [min_v, max_v] band."""
    t_clamped = max(0.0, min(1.0, t))
    return min_v + (max_v - min_v) * t_clamped


def dropout_probability(
    year_level: int, risk_score: float, stall_count: int, config: dict
) -> float:
    """Per-semester probability a student drops out.

    Higher in earlier year levels (real attrition is front-loaded), scaled
    up by the student's latent risk_score and by how many times they've
    already stalled at their current year level. Capped at
    config['dropout']['max_probability'] so no combination of inputs can
    push probability toward certainty.
    """
    d = config["dropout"]
    base_by_level: Dict[int, float] = {int(k): v for k, v in d["base_prob_by_year_level"].items()}
    max_defined_level = max(base_by_level)
    base = base_by_level.get(year_level, base_by_level[max_defined_level])

    risk_mult = _interpolate(d["risk_multiplier_min"], d["risk_multiplier_max"], risk_score)
    stall_mult = 1.0 + stall_count * d["stall_multiplier_per_stall"]

    prob = base * risk_mult * stall_mult
    return min(prob, d["max_probability"])


def graduation_probability(
    tenure_semesters: int, nominal_semesters: int, risk_score: float, config: dict
) -> float:
    """Per-semester probability an eligible student (tenure_semesters >=
    nominal_semesters) graduates this semester. Ramps up the longer a
    student has been eligible without graduating (most people finish
    within a few extra semesters, not indefinitely), and is reduced for
    higher-risk students even once eligible.
    """
    g = config["graduation"]
    extra_semesters = max(0, tenure_semesters - nominal_semesters)
    prob = g["base_probability"] + extra_semesters * g["ramp_per_extra_semester"]
    prob = min(prob, g["max_probability"])
    prob *= (1.0 - g["risk_penalty_factor"] * risk_score)
    return max(0.0, prob)


def stall_probability(risk_score: float, config: dict) -> float:
    """Probability that a student fails to advance a year level at the
    expected year-boundary (stays at the same year_level another year),
    scaled by risk_score."""
    s = config["stall"]
    risk_mult = _interpolate(s["risk_multiplier_min"], s["risk_multiplier_max"], risk_score)
    return min(s["base_probability"] * risk_mult, 1.0)


def shift_probability(year_level: int, config: dict) -> float:
    """Per-semester probability of a program shift. Zero outside years 1-2,
    reflecting real curriculum lock-in after that point."""
    sh = config["shifter"]
    if year_level == 1:
        return sh["probability_year_1"]
    if year_level == 2:
        return sh["probability_year_2"]
    return 0.0


def max_year_level_cap(nominal_duration_years: float, config: dict) -> float:
    """The year_level ceiling for a program: nominal duration plus a
    configured allowance for stalled students, beyond which year_level
    stops incrementing (the student keeps accumulating stall_count and
    therefore rising dropout risk, rather than an unbounded year_level)."""
    return nominal_duration_years + config["stall"]["max_year_level_cap_extra_years"]


def sample_dropout_reason(rng: np.random.Generator, config: dict) -> str:
    """Weighted draw of a dropout reason from config['dropout']['reason_weights'].
    Reuses the same weighted-draw pattern as Day 4's weighted_choice, kept
    local here to avoid a rules-module -> generators-module dependency
    (rules should not depend on the generator that calls them)."""
    weights = config["dropout"]["reason_weights"]
    total = sum(weights.values())
    normalized = {k: v / total for k, v in weights.items()}
    keys = list(normalized.keys())
    probs = list(normalized.values())
    return str(rng.choice(keys, p=probs))
