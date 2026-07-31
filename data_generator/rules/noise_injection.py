"""
data_generator/rules/noise_injection.py

Pure functions that decide/produce realistic messiness: status-text
casing variants, simple typos, and duplicate/late-correction decisions.
Kept separate from the file I/O orchestration (generate_noise.py) for the
same reason every other rules module in this project is separate from its
generator: each function takes plain values in, returns a plain value
out, and is independently unit-testable.

Scope discipline: noise here NEVER touches fields that carry referential
integrity (student_id, program_id, college_id, academic_year,
semester_number). It only touches descriptive text (enrollment_status
casing, home_province spelling) and record-level arrival behavior
(duplicates, late corrections) -- exactly the kind of messiness a real
registrar export has, without ever producing a row that points at a
student/program/college that doesn't exist. See Day 6's validation
checklist: "No noise breaks referential integrity of FKs."
"""

from __future__ import annotations

from typing import Dict

import numpy as np


def apply_status_casing_noise(rng: np.random.Generator, true_status: str, config: dict) -> str:
    """Return a (possibly noisy) text variant of `true_status`, drawn from
    config['status_variants'][true_status]. If the status has no configured
    variants (shouldn't happen for ENROLLED/GRADUATED/DROPPED), the true
    value is returned unchanged rather than raising -- noise injection
    should degrade gracefully, never crash the pipeline over an unmodeled
    status."""
    variants: Dict[str, float] = config.get("status_variants", {}).get(true_status)
    if not variants:
        return true_status
    keys = list(variants.keys())
    probs = list(variants.values())
    return str(rng.choice(keys, p=probs))


def introduce_typo(rng: np.random.Generator, text: str, typo_rate: float) -> str:
    """With probability `typo_rate`, return a mutated version of `text`
    (one adjacent-character swap or one dropped character); otherwise
    return `text` unchanged. Never mutates a string shorter than 2
    characters (nothing meaningful to swap/drop).
    """
    if len(text) < 2 or rng.random() >= typo_rate:
        return text

    mutation = rng.choice(["swap", "drop"])
    pos = int(rng.integers(0, len(text) - 1))
    if mutation == "swap":
        chars = list(text)
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        return "".join(chars)
    else:  # drop
        return text[:pos] + text[pos + 1:]


def should_duplicate(rng: np.random.Generator, config: dict) -> bool:
    return rng.random() < config["duplicate_rate"]


def should_late_correct(rng: np.random.Generator, config: dict) -> bool:
    return rng.random() < config["late_correction_rate"]
