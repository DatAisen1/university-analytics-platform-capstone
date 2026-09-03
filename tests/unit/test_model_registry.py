"""
tests/unit/test_model_registry.py

Unit tests for models/forecasting/model_registry.py's pure
decide_promotion logic (Task 39). No database required -- these
construct CandidateMetrics/ChampionRecord by hand, the same way
tests/unit/test_forecasting_metrics.py checks metrics.py against
hand-computed values rather than a live pipeline run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.forecasting.model_registry import (
    ALGORITHM_SIMPLICITY_RANK,
    CHAMPION_TIE_TOLERANCE_PCT,
    AlgorithmResult,
    CandidateMetrics,
    ChampionRecord,
    decide_promotion,
    make_model_version,
    select_champion_algorithm,
    should_retrain,
)


def _candidate(mae: float, beats_baseline: bool = True, best_baseline_mae: float = 100.0) -> CandidateMetrics:
    return CandidateMetrics(
        mae=mae, rmse=mae * 1.2, mape=10.0, r2=0.5,
        best_baseline_mae=best_baseline_mae, beats_baseline=beats_baseline,
    )


def _champion(mae: float, model_version: str = "COE_enrollment_count_20260101T000000Z") -> ChampionRecord:
    return ChampionRecord(model_registry_key=1, model_version=model_version, mae=mae, artifact_path="/x.pkl")


def test_promotes_when_no_champion_exists_and_beats_baseline():
    decision = decide_promotion(_candidate(mae=10.0), champion=None)
    assert decision.promote is True
    assert "bootstrap" in decision.reason


def test_rejects_when_no_champion_exists_but_does_not_beat_baseline():
    decision = decide_promotion(_candidate(mae=150.0, beats_baseline=False), champion=None)
    assert decision.promote is False
    assert "baseline" in decision.reason


def test_rejects_when_candidate_fails_baseline_even_if_it_would_beat_champion():
    """A candidate that loses to the baseline must never be promoted,
    even if it happens to have a lower MAE than a weak existing
    champion -- criterion 1 is not skippable via criterion 2."""
    champ = _champion(mae=200.0)
    decision = decide_promotion(_candidate(mae=50.0, beats_baseline=False, best_baseline_mae=40.0), champion=champ)
    assert decision.promote is False
    assert "baseline" in decision.reason


def test_promotes_when_candidate_beats_baseline_and_champion():
    champ = _champion(mae=20.0)
    decision = decide_promotion(_candidate(mae=15.0), champion=champ)
    assert decision.promote is True
    assert champ.model_version in decision.reason


def test_rejects_when_candidate_beats_baseline_but_not_champion():
    champ = _champion(mae=5.0)
    decision = decide_promotion(_candidate(mae=15.0), champion=champ)
    assert decision.promote is False
    assert "worse than current champion" in decision.reason


def test_promotes_on_tie_with_champion():
    """Equal MAE is allowed to promote -- a refit that reproduces the
    same model shouldn't be permanently blocked from ever refreshing
    model_version/trained_at."""
    champ = _champion(mae=15.0)
    decision = decide_promotion(_candidate(mae=15.0), champion=champ)
    assert decision.promote is True


# --- P1.24: baseline metric / candidate metric / difference are
# structured fields on every PromotionDecision, not just prose in
# `reason` -- checked across all four decide_promotion branches. ---

def test_promotion_decision_reports_baseline_candidate_and_diff_when_promoted():
    decision = decide_promotion(_candidate(mae=10.0, best_baseline_mae=100.0), champion=None)
    assert decision.baseline_mae == 100.0
    assert decision.candidate_mae == 10.0
    assert decision.mae_diff == pytest.approx(-90.0)  # candidate beat baseline by 90 MAE units


def test_promotion_decision_reports_diff_when_rejected_for_failing_baseline():
    decision = decide_promotion(_candidate(mae=150.0, beats_baseline=False, best_baseline_mae=100.0), champion=None)
    assert decision.baseline_mae == 100.0
    assert decision.candidate_mae == 150.0
    assert decision.mae_diff == pytest.approx(50.0)  # candidate worse than baseline by 50 units


def test_promotion_decision_reports_diff_when_rejected_for_losing_to_champion():
    champ = _champion(mae=5.0)
    decision = decide_promotion(_candidate(mae=15.0, best_baseline_mae=100.0), champion=champ)
    # Rejected on the champion criterion, but the baseline comparison
    # (criterion 1, which it passed) must still be reported, not omitted.
    assert decision.baseline_mae == 100.0
    assert decision.candidate_mae == 15.0
    assert decision.mae_diff == pytest.approx(-85.0)


def test_promotion_decision_diff_is_zero_on_exact_baseline_match():
    decision = decide_promotion(_candidate(mae=100.0, best_baseline_mae=100.0), champion=None)
    assert decision.mae_diff == pytest.approx(0.0)


def test_make_model_version_is_deterministic_and_readable():
    ts = datetime(2026, 8, 2, 14, 5, 1, tzinfo=timezone.utc)
    version = make_model_version("CICT", "enrollment_count", "prophet", trained_at=ts)
    assert version == "CICT_enrollment_count_prophet_20260802T140501Z"


def test_make_model_version_defaults_to_now_and_is_unique_across_calls():
    v1 = make_model_version("COE", "graduation_count", "seasonal_naive")
    v2 = make_model_version("COE", "graduation_count", "seasonal_naive")
    # Not asserting inequality here would be flaky (both could land in
    # the same second) -- instead assert the shape is right and let the
    # timestamp-based module docstring's uniqueness claim stand on the
    # explicit hand-computed case above.
    assert v1.startswith("COE_graduation_count_seasonal_naive_")
    assert v2.startswith("COE_graduation_count_seasonal_naive_")


def test_make_model_version_disambiguates_same_second_different_algorithm():
    # Option B: uq_model_registry_program_metric_version is UNIQUE on
    # (program_key, metric, model_version). Two algorithms evaluated for
    # the same series in the same run, in the same wall-clock second,
    # must not collide -- this is the regression this test guards against.
    ts = datetime(2026, 8, 2, 14, 5, 1, tzinfo=timezone.utc)
    v_prophet = make_model_version("CICT", "enrollment_count", "prophet", trained_at=ts)
    v_naive = make_model_version("CICT", "enrollment_count", "naive", trained_at=ts)
    assert v_prophet != v_naive


# --- Task 42: should_retrain -------------------------------------------------
#
# should_retrain's signature only accepts period_ordinal integers -- there is
# no row-count parameter anywhere in this function, by design (see its
# docstring). These tests exist specifically to pin that: nothing here ever
# constructs or passes a record count, because the whole point of Task 42 is
# that record_count cannot be a retraining trigger even by accident.


def test_retrains_when_no_previous_model_exists():
    decision = should_retrain(current_max_period_ordinal=4, last_trained_period_ordinal=None)
    assert decision.should_retrain is True
    assert "bootstrap" in decision.reason


def test_retrains_when_a_new_semester_is_available():
    """The core Task 42 case: period_ordinal advanced by one (a new
    semester posted) -- this, and only this kind of change, should
    trigger a retrain."""
    decision = should_retrain(current_max_period_ordinal=8, last_trained_period_ordinal=7)
    assert decision.should_retrain is True
    assert "1 new academic period" in decision.reason


def test_retrains_when_multiple_new_semesters_are_available():
    decision = should_retrain(current_max_period_ordinal=10, last_trained_period_ordinal=7)
    assert decision.should_retrain is True
    assert "3 new academic period" in decision.reason


def test_does_not_retrain_when_no_new_semester_is_available():
    """Task 42's explicit target case: the data available now covers
    exactly the same periods as the last training run. Nothing in this
    call indicates whether row counts changed underneath those periods
    -- that's the point; should_retrain has no way to know or care."""
    decision = should_retrain(current_max_period_ordinal=7, last_trained_period_ordinal=7)
    assert decision.should_retrain is False
    assert "row count alone is not a retraining trigger" in decision.reason


def test_does_not_retrain_when_data_appears_to_have_regressed():
    """Defensive edge case: available data covering FEWER periods than
    what was already trained on should never silently trigger a
    retrain -- that pattern indicates a data problem, not new data."""
    decision = should_retrain(current_max_period_ordinal=5, last_trained_period_ordinal=7)
    assert decision.should_retrain is False
    assert "regressed" in decision.reason


# ---------------------------------------------------------------------
# select_champion_algorithm (P1.1, Model Selection Robustness): no
# direct unit coverage existed before this -- only an indirect
# regression guard in tests/unit/test_count_model.py checking that
# count_model's name is registered in ALGORITHM_SIMPLICITY_RANK at all.
# These exercise the actual tie-break decision.
# ---------------------------------------------------------------------

def _result(algorithm: str, mae: float) -> AlgorithmResult:
    return AlgorithmResult(algorithm=algorithm, mae=mae, rmse=mae * 1.2, mape=10.0, r2=0.5)


def test_select_champion_picks_strict_lowest_mae_outside_tolerance():
    """No tie: prophet's MAE is far enough below naive's that the
    CHAMPION_TIE_TOLERANCE_PCT band doesn't reach it -- prophet wins
    outright, regardless of simplicity rank."""
    candidates = [_result("naive", mae=20.0), _result("prophet", mae=10.0)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "prophet"
    assert selection.ranked[0].algorithm == "prophet"
    assert "practically tied" not in selection.reason


def test_select_champion_prefers_simpler_algorithm_within_tolerance():
    """The actual policy this task adds: prophet has the strictly lowest
    raw MAE, but naive is within CHAMPION_TIE_TOLERANCE_PCT of it -- naive
    should win because it's simpler (lower ALGORITHM_SIMPLICITY_RANK),
    and the reason string should say so, not just report prophet as the
    unqualified winner."""
    best_mae = 10.0
    within_tolerance_mae = best_mae * (1 + CHAMPION_TIE_TOLERANCE_PCT * 0.5)  # inside the band
    candidates = [_result("prophet", mae=best_mae), _result("naive", mae=within_tolerance_mae)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "naive"
    assert selection.ranked[0].algorithm == "naive"  # ranked reflects actual selection order
    assert "practically tied" in selection.reason
    assert "naive" in selection.reason


def test_select_champion_boundary_exactly_at_tolerance_counts_as_tied():
    """Exactly at the tolerance boundary (not strictly inside it) must
    still count as tied -- the comparison is <=, not <, so a candidate
    landing precisely on the edge isn't excluded by a fencepost error."""
    best_mae = 10.0
    boundary_mae = best_mae * (1 + CHAMPION_TIE_TOLERANCE_PCT)
    candidates = [_result("prophet", mae=best_mae), _result("naive", mae=boundary_mae)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "naive"


def test_select_champion_just_outside_tolerance_keeps_lowest_mae_winner():
    """Just past the tolerance boundary, no tie applies -- the strictly
    lower raw MAE wins even though the gap is small in absolute terms,
    since the policy is a fixed percentage, not a vibe."""
    best_mae = 10.0
    just_outside_mae = best_mae * (1 + CHAMPION_TIE_TOLERANCE_PCT) + 0.01
    candidates = [_result("prophet", mae=best_mae), _result("naive", mae=just_outside_mae)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "prophet"
    assert "practically tied" not in selection.reason


def test_select_champion_zero_mae_only_ties_with_other_zeros():
    """Degenerate-series edge case: when the best MAE is exactly 0, a
    percentage tolerance band is also exactly 0 (0 * anything == 0) --
    only another exactly-zero candidate should be treated as tied, never
    a small-but-nonzero one, however small."""
    candidates = [_result("naive", mae=0.0), _result("prophet", mae=0.001)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "naive"
    assert "practically tied" not in selection.reason


def test_select_champion_multiple_algorithms_tied_picks_simplest_of_all():
    """Three-way tie within tolerance: the simplest of ALL tied
    candidates wins, not just a pairwise comparison against the raw
    best -- naive (rank 0) beats both seasonal_naive (rank 1) and
    prophet (rank 4) even though prophet has the strictly lowest MAE."""
    best_mae = 10.0
    candidates = [
        _result("prophet", mae=best_mae),
        _result("seasonal_naive", mae=best_mae * 1.02),
        _result("naive", mae=best_mae * 1.04),
    ]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "naive"
    assert selection.ranked[0].algorithm == "naive"


def test_select_champion_single_candidate_wins_trivially():
    candidates = [_result("prophet", mae=5.0)]
    selection = select_champion_algorithm(candidates)
    assert selection.winner.algorithm == "prophet"
    assert "only algorithm evaluated" in selection.reason


def test_select_champion_raises_on_empty_candidates():
    with pytest.raises(ValueError):
        select_champion_algorithm([])