"""
tests/unit/test_deploy_forecast.py

Task 51 (ML tests: forecast generation): models/forecasting/deploy_forecast.py
orchestrates the full Retrain-gate -> Candidate -> Evaluate -> Compare ->
Promote -> Write-back workflow (Tasks 39-42) and is the only piece of that
chain that actually writes to gold.fact_forecast. model_registry.py's pure
promotion/retrain rules and train_prophet.py's fit/evaluate functions each
already have dedicated unit tests -- this file covers the orchestration
function itself (`deploy_forecasts`) and its small pure helpers, with every
collaborator monkeypatched so no live Postgres/Prophet run is required.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from models.forecasting import deploy_forecast
from models.forecasting.model_registry import CandidateMetrics, PromotionDecision, RetrainDecision
from pipelines.common.errors import ForecastError


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_period_ordinal_matches_build_dimensions_convention():
    assert deploy_forecast._period_ordinal(2021, 1) == 0
    assert deploy_forecast._period_ordinal(2021, 2) == 1
    assert deploy_forecast._period_ordinal(2022, 1) == 2


def test_next_target_period_advances_within_same_academic_year():
    year, semester, ordinal = deploy_forecast._next_target_period(6)
    assert (year, semester, ordinal) == (2024, 2, 7)


def test_next_target_period_rolls_into_a_new_academic_year():
    year, semester, ordinal = deploy_forecast._next_target_period(7)
    assert (year, semester, ordinal) == (2025, 1, 8)


def test_semester_to_date_matches_calendar_convention():
    assert deploy_forecast._semester_to_date(2024, 1) == "2024-01-01"
    assert deploy_forecast._semester_to_date(2024, 2) == "2024-07-01"


class _FakeModel:
    def __init__(self, yhat, yhat_lower, yhat_upper):
        self._row = {"yhat": yhat, "yhat_lower": yhat_lower, "yhat_upper": yhat_upper}

    def predict(self, future_df):
        return pd.DataFrame([self._row])


def test_forecast_next_period_clips_negative_values_to_zero():
    model = _FakeModel(yhat=-5.0, yhat_lower=-10.0, yhat_upper=3.0)
    yhat, lower, upper = deploy_forecast._forecast_next_period(model, "2025-01-01")
    assert (yhat, lower, upper) == (0.0, 0.0, 3.0)


def test_forecast_next_period_passes_through_positive_values():
    model = _FakeModel(yhat=120.0, yhat_lower=100.0, yhat_upper=140.0)
    yhat, lower, upper = deploy_forecast._forecast_next_period(model, "2025-01-01")
    assert (yhat, lower, upper) == (120.0, 100.0, 140.0)


# --------------------------------------------------------------------------
# deploy_forecasts orchestration
# --------------------------------------------------------------------------

def _series_frame() -> pd.DataFrame:
    """One program x eight observed periods, enough for both TARGET_METRICS.

    Program-grain (P1 Data Science Recovery fix / migration 0013):
    deploy_forecast.py iterates series_df["program_id"], and its adapter
    call (to_prophet_frame) requires a real 'ds' column -- both were
    missing from this fixture's old college-grain shape, which predated
    that migration and was never updated to match. Not stubbed out:
    to_prophet_frame is cheap and pure, so real validation exercising
    real fixture data is more honest coverage than mocking it too.
    """
    rows = []
    for ordinal in range(8):
        year = 2021 + ordinal // 2
        semester = 1 if ordinal % 2 == 0 else 2
        rows.append(
            {
                "program_id": "BSCS",
                "program_key": 1,
                "college_id": "COE",
                "college_key": 1,
                "period_ordinal": ordinal,
                "academic_year": year,
                "semester_number": semester,
                "ds": deploy_forecast._semester_to_date(year, semester),
                "enrollment_count": 100 + ordinal,
                "graduation_count": 5 + ordinal,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def patched_fit_and_record(monkeypatch):
    """Stubs the two collaborators every deploy_forecasts() branch touches
    regardless of the retrain/promotion outcome, so tests only need to set
    the decision-specific patches for their scenario."""
    monkeypatch.setattr(deploy_forecast, "fit_prophet", lambda train_df: _FakeModel(50.0, 40.0, 60.0))
    monkeypatch.setattr(
        deploy_forecast,
        "record_candidate",
        lambda engine, program_key, metric, model_version, candidate, training_meta, artifact_path, decision, **kw: 999,
    )
    write_calls = []
    monkeypatch.setattr(
        deploy_forecast,
        "_write_forecast_row",
        lambda engine, **kwargs: write_calls.append(kwargs),
    )
    return write_calls


def _patch_common(monkeypatch, *, retrain: bool, promote: bool):
    monkeypatch.setattr(deploy_forecast, "load_series", lambda engine: _series_frame())
    monkeypatch.setattr(deploy_forecast, "get_last_trained_period_ordinal", lambda engine, program_key, metric: None)
    monkeypatch.setattr(
        deploy_forecast,
        "should_retrain",
        lambda current_max, last_trained: RetrainDecision(should_retrain=retrain, reason="fixture"),
    )
    monkeypatch.setattr(
        deploy_forecast,
        "walk_forward_evaluate",
        lambda program_series, metric, test_ordinals: {
            "naive": {"actual": [1.0, 2.0], "predicted": [1.0, 2.0]},
            "historical_avg": {"actual": [1.0, 2.0], "predicted": [1.0, 2.0]},
            "prophet": {"actual": [1.0, 2.0], "predicted": [1.0, 2.0]},
        },
    )
    monkeypatch.setattr(
        deploy_forecast,
        "compute_metrics_for_model",
        lambda actual, predicted: {"mae": 1.0, "rmse": 1.0, "mape": 5.0, "r2": 0.9},
    )
    monkeypatch.setattr(deploy_forecast, "get_current_champion", lambda engine, program_key, metric: None)
    monkeypatch.setattr(
        deploy_forecast,
        "decide_promotion",
        lambda candidate, champion: PromotionDecision(promote=promote, reason="fixture decision"),
    )
    monkeypatch.setattr(
        deploy_forecast, "make_model_version", lambda program_id, metric: f"{program_id}_{metric}_v1"
    )


def test_deploy_forecasts_skips_series_when_retrain_gate_declines(monkeypatch, patched_fit_and_record, tmp_path):
    _patch_common(monkeypatch, retrain=False, promote=True)

    results = deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    assert len(results) > 0
    assert all(r.retrained is False for r in results)
    assert all(r.promoted is False for r in results)
    assert patched_fit_and_record == []  # no forecast row written when the gate declines


def test_deploy_forecasts_does_not_write_forecast_row_when_candidate_rejected(
    monkeypatch, patched_fit_and_record, tmp_path
):
    _patch_common(monkeypatch, retrain=True, promote=False)

    results = deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    assert all(r.retrained is True for r in results)
    assert all(r.promoted is False for r in results)
    assert all(r.yhat is None for r in results)
    assert patched_fit_and_record == []  # rejected candidates never reach the write-back step


def test_deploy_forecasts_writes_forecast_row_and_marks_promoted_when_candidate_wins(
    monkeypatch, patched_fit_and_record, tmp_path
):
    _patch_common(monkeypatch, retrain=True, promote=True)

    results = deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    assert all(r.retrained is True for r in results)
    assert all(r.promoted is True for r in results)
    assert all(r.yhat == 50.0 for r in results)
    assert all(r.target_academic_year is not None and r.target_semester_number is not None for r in results)

    # one gold.fact_forecast write per (college, metric) series
    from models.forecasting.train_prophet import TARGET_METRICS
    assert len(patched_fit_and_record) == len(TARGET_METRICS)
    for call in patched_fit_and_record:
        assert call["yhat"] == 50.0
        assert call["target_period_ordinal"] == 8  # next period after the 8 observed (ordinals 0-7)


def test_deploy_forecasts_writes_model_artifact_to_disk_even_when_rejected(
    monkeypatch, patched_fit_and_record, tmp_path
):
    """A rejected candidate is still fit on full history and pickled, so its
    walk-forward result stays reproducible/inspectable later (see module
    docstring) -- even though nothing is promoted or written to Postgres."""
    _patch_common(monkeypatch, retrain=True, promote=False)

    deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    artifacts = list(tmp_path.glob("*.pkl"))
    assert len(artifacts) > 0


def test_deploy_forecasts_wraps_unexpected_errors_in_forecast_error(monkeypatch, tmp_path):
    def _boom(engine):
        raise RuntimeError("warehouse unreachable")

    monkeypatch.setattr(deploy_forecast, "load_series", _boom)

    with pytest.raises(ForecastError):
        deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)


def test_deploy_forecasts_propagates_forecast_error_without_rewrapping(monkeypatch, tmp_path):
    def _raise_forecast_error(engine):
        raise ForecastError("already classified", stage="Forecast Deployment")

    monkeypatch.setattr(deploy_forecast, "load_series", _raise_forecast_error)

    with pytest.raises(ForecastError, match="already classified"):
        deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)


# --------------------------------------------------------------------------
# P1 (Forecast Output Contract): dataset_fingerprint provenance
# --------------------------------------------------------------------------

@pytest.fixture
def patched_fit_and_record_capturing_training_meta(monkeypatch):
    """Same as patched_fit_and_record, but keeps the actual TrainingMetadata
    passed to record_candidate for each call, instead of discarding it --
    needed to assert on dataset_fingerprint specifically."""
    monkeypatch.setattr(deploy_forecast, "fit_prophet", lambda train_df: _FakeModel(50.0, 40.0, 60.0))
    training_meta_calls = []

    def _fake_record_candidate(engine, program_key, metric, model_version, candidate, training_meta, artifact_path, decision, **kw):
        training_meta_calls.append(training_meta)
        return 999

    monkeypatch.setattr(deploy_forecast, "record_candidate", _fake_record_candidate)
    monkeypatch.setattr(deploy_forecast, "_write_forecast_row", lambda engine, **kwargs: None)
    return training_meta_calls


def test_deploy_forecasts_populates_dataset_fingerprint_on_every_candidate(
    monkeypatch, patched_fit_and_record_capturing_training_meta, tmp_path
):
    _patch_common(monkeypatch, retrain=True, promote=True)

    deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    training_meta_calls = patched_fit_and_record_capturing_training_meta
    # one candidate per (program, metric) -- _series_frame() has one
    # program and models.forecasting.train_prophet.TARGET_METRICS has 2
    assert len(training_meta_calls) == 2
    for meta in training_meta_calls:
        assert meta.dataset_fingerprint
        assert isinstance(meta.dataset_fingerprint, str)


def test_deploy_forecasts_reuses_the_same_dataset_fingerprint_across_candidates_in_one_run(
    monkeypatch, patched_fit_and_record_capturing_training_meta, tmp_path
):
    """Every candidate trained in the SAME deploy_forecasts() call read the
    same load_series() pull, so they must all be stamped with the same
    fingerprint -- computed once, not recomputed (and potentially drifting)
    per series."""
    _patch_common(monkeypatch, retrain=True, promote=True)

    deploy_forecast.deploy_forecasts(engine=object(), artifacts_dir=tmp_path)

    fingerprints = {meta.dataset_fingerprint for meta in patched_fit_and_record_capturing_training_meta}
    assert len(fingerprints) == 1