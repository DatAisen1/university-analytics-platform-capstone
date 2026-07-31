"""
pipelines/silver/progression_validation.py

Silver-stage business rule: detect IMPOSSIBLE year_level transitions
between a student's own chronologically-adjacent enrollment records
(P0 #13). This needs no program-duration data -- it's a structural
impossibility check, independent of Super Senior/nominal-duration logic
(pipelines/common/academic_periods.py), which is a separate, derived-label
concern applied later, in Gold/marts.

Rule: given a student's two enrollment rows in CONSECUTIVE observed
semesters (period_index difference == 1 -- see academic_periods.
academic_period_index), year_level must either stay the same (a stall)
or increase by exactly 1 (normal progression). Anything else is
impossible given this project's generator, which only ever holds or
increments by 1 at a year boundary:
  - a DECREASE (Senior -> Junior, Sophomore -> Freshman) is never valid --
    there is no "academic renewal"/re-admission-at-lower-standing concept
    modeled anywhere in this project. If one is ever introduced, it must
    be justified by a dedicated flag on the record, not silently allowed
    here.
  - a JUMP of 2+ levels in one semester step (Freshman -> Senior) is never
    valid -- the fastest possible advance is +1 level per year-boundary.

Deliberately NOT checked: transitions across a GAP (period_index
difference > 1) in a student's records. A gap is a missing-data question,
not a progression-legality question -- validating "legality" across an
unknown-length absence would produce false positives, not real
findings. A gap is a separate, pre-existing kind of anomaly this module
does not claim to catch.

A program shift (fact_shifter) does NOT reset year_level in this
project's generator (see generate_progression.simulate_student) -- so no
special-casing is needed here for shifted students.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from pipelines.common.academic_periods import academic_period_index


def find_impossible_year_level_transitions(enrollment_df: pd.DataFrame) -> pd.DataFrame:
    """Return the subset of `enrollment_df` whose year_level transition
    from the student's immediately-preceding CONSECUTIVE semester is
    impossible (a decrease, or a jump of more than 1 level). Adds a
    `_transition_violation` column describing the bad transition
    (e.g. "4 -> 2"). Returns an empty frame (same columns + the new one)
    if nothing is wrong -- callers should check `.empty`, not truthiness.
    """
    if enrollment_df.empty:
        empty = enrollment_df.iloc[0:0].copy()
        empty["_transition_violation"] = pd.Series(dtype=str)
        return empty

    if "year_level" not in enrollment_df.columns:
        empty = enrollment_df.iloc[0:0].copy()
        empty["_transition_violation"] = pd.Series(dtype=str)
        return empty

    working = enrollment_df.copy()
    working["_period_index"] = working.apply(
        lambda r: academic_period_index(
            r["academic_year"],
            r.get("semester_number", r.get("semester_name")),
        ),
        axis=1,
    )
    working = working.sort_values(["student_id", "_period_index"]).reset_index(drop=True)

    grouped = working.groupby("student_id", sort=False)
    working["_prev_period_index"] = grouped["_period_index"].shift(1)
    working["_prev_year_level"] = grouped["year_level"].shift(1)

    is_consecutive = working["_period_index"] == (working["_prev_period_index"] + 1)
    decreased = working["year_level"] < working["_prev_year_level"]
    jumped = working["year_level"] > (working["_prev_year_level"] + 1)

    violation_mask = is_consecutive & (decreased | jumped)
    violations = working[violation_mask].copy()
    if violations.empty:
        empty = violations.copy()
        empty["_transition_violation"] = pd.Series(dtype=str)
        return empty.drop(columns=["_period_index", "_prev_period_index", "_prev_year_level"])

    violations["_transition_violation"] = violations.apply(
        lambda r: f"{int(r['_prev_year_level'])} -> {int(r['year_level'])}", axis=1
    )
    return violations.drop(columns=["_period_index", "_prev_period_index", "_prev_year_level"])


def check_year_level_progression(enrollment_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Quarantine any enrollment row whose year_level transition from the
    student's prior CONSECUTIVE semester is impossible. Matches the
    (valid_df, quarantined_df) signature convention already used by
    pipelines/silver/validate_and_dedupe.py's other check_* functions.
    """
    violations = find_impossible_year_level_transitions(enrollment_df)
    if violations.empty:
        return enrollment_df, enrollment_df.iloc[0:0].copy()

    semester_column = "semester_number" if "semester_number" in enrollment_df.columns else "semester_name"
    bad_keys = set(
        zip(violations["student_id"], violations["academic_year"], violations[semester_column])
    )

    def _is_flagged(row) -> bool:
        return (row["student_id"], row["academic_year"], row[semester_column]) in bad_keys

    mask = enrollment_df.apply(_is_flagged, axis=1)
    quarantined = enrollment_df[mask].copy()
    quarantined["_quarantine_reason"] = "impossible year_level transition from prior consecutive semester"
    valid = enrollment_df[~mask].copy()
    return valid, quarantined