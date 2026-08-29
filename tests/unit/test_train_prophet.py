"""
tests/unit/test_train_prophet.py

Tests for models/forecasting/train_prophet.py: the pure date-mapping
function, walk-forward fold structure, and (skipped if Postgres
unreachable) full integration tests against the live warehouse.

P1 fix: series are now (program, metric), read from
gold.ml_program_forecast_features -- not (college, metric) from
gold.fact_institution_kpi. The integration tests below assert against
gold.dim_program's actual row count (queried at fixture time, not
hardcoded) rather than a guessed number, since program count depends on
configs/programs.yaml and this suite has no live Postgres to verify a
literal count against in this environment -- see REMAINING ISSUES in
this task's execution report for what still needs a real run to confirm.
"""

import os
import pickle
import sys

import pandas as pd
import pytest

from pipelines.common.errors import ModelEvaluationError, ModelTrainingError
from models.forecasting.train_prophet import (
    MIN_HISTORY_PERIODS,
    evaluate_all_series,
    fit_prophet,
    has_sufficient_history,
    load_model,
    predict_point,
    semester_to_date,
    derive_test_period_ordinals,
    to_prophet_frame,
)
TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}
PIPELINE_WRITER_PASSWORD = os.environ.get("TEST_PIPELINE_WRITER_PASSWORD", "pw_pipeline123")


class _FakeProphet:
    """Deterministic stand-in for prophet.Prophet -- same
    monkeypatch-the-`prophet`-module technique already used below by
    test_fit_prophet_wraps_training_failures_in_model_training_error,
    extended with a fit/predict that actually works (not just raises),
    so the P2.2/P2.3 tests below can exercise a real save -> load ->
    predict round trip without pulling in the heavy real dependency.
    Prediction is a plain function of the training data, so a bug in
    the save/load path would surface as a MISMATCHED yhat, not just a
    pickling exception.
    """

    def fit(self, train_df):
        self._mean_y = float(train_df["y"].mean())
        return self

    def predict(self, future_df):
        return pd.DataFrame(
            {
                "ds": future_df["ds"],
                "yhat": [self._mean_y] * len(future_df),
                "yhat_lower": [self._mean_y - 1.0] * len(future_df),
                "yhat_upper": [self._mean_y + 1.0] * len(future_df),
            }
        )


def test_semester_to_date_semester_1_is_january():
    assert semester_to_date(2021, 1) == "2021-01-01"


def test_semester_to_date_semester_2_is_july():
    assert semester_to_date(2021, 2) == "2021-07-01"


def test_semester_to_date_matches_dim_calendar_convention():
    assert semester_to_date(2024, 1)[:4] == "2024"
    assert semester_to_date(2024, 2)[:4] == "2024"


def test_period_ordinals_matches_current_six_period_dataset():
    # Current OBSERVED_ACADEMIC_YEARS = [2021, 2022, 2023] -> 6 periods,
    # ordinals 0-5 -> this is the exact fold table docs/10_Forecasting.md
    # Section 5 documents. Same result the old [3, 4, 5] literal gave --
    # but now derived, not hardcoded.
    assert derive_test_period_ordinals(5) == [3, 4, 5]


def test_period_ordinals_always_returns_three_points():
    assert len(derive_test_period_ordinals(3)) == 3
    assert len(derive_test_period_ordinals(10)) == 3


def test_period_ordinals_shifts_when_history_grows():
    # P1.14: this is the case the old hardcoded literal could NOT
    # handle -- a 5th observed academic year (10 periods, ordinals
    # 0-9) must shift the fold window forward, not silently keep
    # testing against stale ordinals 3-5.
    assert derive_test_period_ordinals(9) == [7, 8, 9]
    assert derive_test_period_ordinals(9) != derive_test_period_ordinals(5)


def test_min_history_periods_covers_fold_one_train_plus_test():
    # Fold 1 needs period_ordinal 0-2 (train) + 3 (test) = 4 distinct periods.
    assert MIN_HISTORY_PERIODS == 4


def test_has_sufficient_history_true_when_all_periods_present():
    series = pd.DataFrame({"period_ordinal": [0, 1, 2, 3, 4, 5]})
    assert has_sufficient_history(series) is True


def test_has_sufficient_history_false_for_sparse_program():
    # A newly-established program with only 2 semesters of enrollment.
    series = pd.DataFrame({"period_ordinal": [4, 5]})
    assert has_sufficient_history(series) is False


def test_fit_prophet_wraps_training_failures_in_model_training_error(monkeypatch):
    class BrokenProphet:
        def fit(self, train_df):
            raise RuntimeError("boom")

    monkeypatch.setitem(__import__("sys").modules, "prophet", type("ProphetModule", (), {"Prophet": BrokenProphet}))

    with pytest.raises(ModelTrainingError) as exc:
        fit_prophet(pd.DataFrame({"ds": ["2021-01-01"], "y_col": [1.0]}))

    assert exc.value.category.value == "MODEL_TRAINING_ERROR"
    assert "Prophet training failed" in str(exc.value)


def test_evaluate_all_series_wraps_evaluation_failures_in_model_evaluation_error(monkeypatch):
    monkeypatch.setattr(
        "models.forecasting.train_prophet.load_series",
        lambda engine: pd.DataFrame({
            "program_id": ["BSCS"] * 4,
            "program_key": [1] * 4,
            "college_id": ["CICT"] * 4,
            "college_key": [1] * 4,
            "period_ordinal": [0, 1, 2, 3],
            "academic_year": [2021, 2021, 2022, 2022],
            "semester_number": [1, 2, 1, 2],
            "enrollment_count": [1, 1, 1, 1],
            "graduation_count": [1, 1, 1, 1],
            "ds": ["2021-01-01", "2021-07-01", "2022-01-01", "2022-07-01"],
        }),
    )
    monkeypatch.setattr(
        "models.forecasting.train_prophet.walk_forward_evaluate",
        lambda entity_series, metric, test_ordinals: (_ for _ in ()).throw(RuntimeError("bad fold")),
    )

    with pytest.raises(ModelEvaluationError) as exc:
        evaluate_all_series(engine=None)

    assert exc.value.category.value == "MODEL_EVALUATION_ERROR"
    assert "Walk-forward evaluation failed" in str(exc.value)


# --- P1.13: to_prophet_frame adapter tests (no Postgres required) ---

def test_to_prophet_frame_valid_input_produces_ds_y_sorted_by_ds():
    series = pd.DataFrame({
        "ds": ["2022-01-01", "2021-01-01", "2021-07-01"],
        "enrollment_count": [30, 10, 20],
    })
    frame = to_prophet_frame(series, "enrollment_count")
    assert list(frame.columns) == ["ds", "y"]
    assert list(frame["y"]) == [10, 20, 30]  # sorted by ds, not input order
    assert pd.api.types.is_datetime64_any_dtype(frame["ds"])


def test_to_prophet_frame_missing_ds_column_raises():
    series = pd.DataFrame({"enrollment_count": [10, 20]})
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "'ds' column missing" in str(exc.value)


def test_to_prophet_frame_null_ds_raises():
    # A literal None/NaN in ds converts to NaT (not a raised exception --
    # pd.to_datetime(errors="raise") only raises on unparseable strings,
    # not already-missing values), so this is a distinct code path from
    # test_to_prophet_frame_unparseable_ds_raises above and needs its own
    # coverage per P1.11 ("non-null").
    series = pd.DataFrame({
        "ds": [None, "2021-07-01"],
        "enrollment_count": [10, 20],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "null ds value" in str(exc.value)


def test_to_prophet_frame_missing_metric_column_raises():
    series = pd.DataFrame({"ds": ["2021-01-01", "2021-07-01"]})
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "not present in series" in str(exc.value)


def test_to_prophet_frame_null_y_raises():
    series = pd.DataFrame({
        "ds": ["2021-01-01", "2021-07-01"],
        "enrollment_count": [10, None],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "null y value" in str(exc.value)


def test_to_prophet_frame_unparseable_ds_raises():
    series = pd.DataFrame({
        "ds": ["2021-01-01", "not-a-date"],
        "enrollment_count": [10, 20],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "not convertible to datetime" in str(exc.value)


def test_to_prophet_frame_non_numeric_y_raises():
    series = pd.DataFrame({
        "ds": ["2021-01-01", "2021-07-01"],
        "enrollment_count": ["ten", "twenty"],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "not numeric" in str(exc.value)


def test_to_prophet_frame_negative_y_raises():
    series = pd.DataFrame({
        "ds": ["2021-01-01", "2021-07-01"],
        "enrollment_count": [10, -5],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "negative value" in str(exc.value)


def test_to_prophet_frame_duplicate_ds_raises():
    series = pd.DataFrame({
        "ds": ["2021-01-01", "2021-01-01"],
        "enrollment_count": [10, 15],
    })
    with pytest.raises(ModelTrainingError) as exc:
        to_prophet_frame(series, "enrollment_count")
    assert "duplicate ds" in str(exc.value)


def test_to_prophet_frame_unsorted_periods_are_sorted_not_rejected():
    # Unsorted input is a normal case (callers don't always pre-sort) --
    # the adapter's job is to sort it, not reject it. Only NON-recoverable
    # contract violations (nulls, dupes, bad dtype) should raise.
    series = pd.DataFrame({
        "ds": ["2022-01-01", "2021-01-01"],
        "graduation_count": [5, 1],
    })
    frame = to_prophet_frame(series, "graduation_count")
    assert list(frame["ds"]) == sorted(frame["ds"])


# --- P2.2/P2.3 (MLOps Simplification): artifact retrieval + forecast
# reproducibility. No Postgres or real prophet dependency required -- see
# _FakeProphet above. ---

def test_load_model_round_trips_a_saved_artifact(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "prophet", type("ProphetModule", (), {"Prophet": _FakeProphet}))
    train_df = pd.DataFrame({"ds": ["2021-01-01", "2021-07-01"], "y": [10.0, 20.0]})
    model = fit_prophet(train_df)

    artifact_path = tmp_path / "test_model.pkl"
    with artifact_path.open("wb") as f:
        pickle.dump(model, f)

    loaded = load_model(artifact_path)

    # The loaded model must be usable for prediction, not just
    # unpickle-without-erroring -- P2.2 asks whether a trained model
    # "can be retrieved", i.e. retrieved AND used, not merely stored.
    assert predict_point(loaded, "2022-01-01") == predict_point(model, "2022-01-01")


def test_load_model_missing_artifact_raises_clear_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.pkl"
    with pytest.raises(FileNotFoundError, match="Model artifact not found"):
        load_model(missing_path)


def test_saved_model_predictions_are_reproducible_across_loads(tmp_path, monkeypatch):
    """P2.3: for a FIXED model_version (i.e. a specific artifact already
    on disk), loading it and forecasting the same target period twice
    must produce equivalent results within tolerance. This is
    inference-time determinism -- reading the same bytes back should
    never silently change what the model predicts -- not a claim that
    retraining Prophet from scratch on the same data converges to
    bit-identical parameters (Prophet's optimizer isn't guaranteed to be
    exactly reproducible across runs/environments, so that's a separate,
    much weaker guarantee this test does not make)."""
    monkeypatch.setitem(sys.modules, "prophet", type("ProphetModule", (), {"Prophet": _FakeProphet}))
    train_df = pd.DataFrame(
        {"ds": ["2021-01-01", "2021-07-01", "2022-01-01"], "y": [10.0, 12.0, 14.0]}
    )
    model = fit_prophet(train_df)
    artifact_path = tmp_path / "reproducibility_model.pkl"
    with artifact_path.open("wb") as f:
        pickle.dump(model, f)

    first_load = load_model(artifact_path)
    second_load = load_model(artifact_path)

    first_yhat = predict_point(first_load, "2022-07-01")
    second_yhat = predict_point(second_load, "2022-07-01")

    assert first_yhat == pytest.approx(second_yhat, abs=1e-9)


def _report_row(
    program_id, college_id, metric, prophet_mae, best_baseline_mae, beats,
    seasonal_naive_mae=8.0, prophet_mape=None,
):
    """Builds one row shaped like evaluate_all_series' output -- just the
    columns write_evaluation_report / summarize_graduation_count_reconciliation
    actually read, so these tests stay decoupled from the full evaluation
    pipeline (and therefore don't need Postgres)."""
    return {
        "program_id": program_id,
        "college_id": college_id,
        "metric": metric,
        "prophet_mae": prophet_mae,
        "prophet_mape": prophet_mape,
        "naive_mae": best_baseline_mae + 5,
        "historical_avg_mae": best_baseline_mae + 3,
        "seasonal_naive_mae": seasonal_naive_mae,
        "best_baseline_mae": best_baseline_mae,
        "mae_diff": prophet_mae - best_baseline_mae,
        "prophet_r2": 0.85,
        "prophet_beats_best_baseline": beats,
    }


class TestWriteEvaluationReportMetricBreakdown:
    """P1.5: write_evaluation_report's single combined headline ('29 of
    74') mixes enrollment_count and graduation_count, which is honest
    about the total but hides that the two metrics behave very
    differently. These tests exercise the per-metric breakdown directly
    with a synthetic DataFrame, so they need no live Postgres."""

    def test_breakdown_reports_each_metric_separately(self, tmp_path):
        from models.forecasting.train_prophet import write_evaluation_report

        report_df = pd.DataFrame(
            [
                # enrollment_count: 2 of 2 beat baseline
                _report_row("BSCS", "CICT", "enrollment_count", 5.0, 10.0, True),
                _report_row("BSIT", "CICT", "enrollment_count", 6.0, 9.0, True),
                # graduation_count: 0 of 1 beat baseline
                _report_row("BSCS", "CICT", "graduation_count", 12.0, 10.0, False),
            ]
        )
        _, md_path = write_evaluation_report(report_df, artifacts_dir=tmp_path)
        text = md_path.read_text(encoding="utf-8")

        # Overall headline still present (3 series total, 2 beat baseline).
        assert "Prophet beats the best baseline on **2 of 3** series" in text
        # But the breakdown must disaggregate what the headline hides.
        assert "`enrollment_count`: 2 of 2 (100%)" in text
        assert "`graduation_count`: 0 of 1 (0%)" in text

    def test_breakdown_is_omitted_gracefully_for_empty_report(self, tmp_path):
        from models.forecasting.train_prophet import write_evaluation_report

        empty_df = pd.DataFrame(
            columns=[
                "program_id", "college_id", "metric", "prophet_mae", "naive_mae",
                "historical_avg_mae", "seasonal_naive_mae", "best_baseline_mae",
                "mae_diff", "prophet_r2", "prophet_beats_best_baseline",
            ]
        )
        # Must not raise ZeroDivisionError, same guarantee the original
        # headline logic already gave the total==0 case.
        _, md_path = write_evaluation_report(empty_df, artifacts_dir=tmp_path)
        text = md_path.read_text(encoding="utf-8")
        assert "Prophet beats the best baseline on **0 of 0** series (N/A)" in text
        assert "No series were evaluated" in text

    def test_breakdown_percentage_rounds_per_metric_not_from_the_overall_total(self, tmp_path):
        """Regression guard: a naive implementation might compute each
        metric's percentage using the OVERALL total instead of that
        metric's own subtotal, silently under-reporting every metric
        whose row count differs from the grand total."""
        from models.forecasting.train_prophet import write_evaluation_report

        report_df = pd.DataFrame(
            [
                _report_row("BSCS", "CICT", "enrollment_count", 5.0, 10.0, True),
                _report_row("BSIT", "CICT", "enrollment_count", 5.0, 10.0, True),
                _report_row("BSCE", "COE", "enrollment_count", 5.0, 10.0, True),
                _report_row("BSCS", "CICT", "graduation_count", 12.0, 10.0, False),
            ]
        )
        _, md_path = write_evaluation_report(report_df, artifacts_dir=tmp_path)
        text = md_path.read_text(encoding="utf-8")
        # enrollment_count: 3 of 3 -> 100%, computed against its OWN
        # subtotal (3), not the grand total (4), which would wrongly
        # read 75%.
        assert "`enrollment_count`: 3 of 3 (100%)" in text
        assert "`graduation_count`: 0 of 1 (0%)" in text


class TestGraduationCountReconciliation:
    """P1.6: summarize_graduation_count_reconciliation() quantifies how
    many graduation_count series without a Prophet champion this cycle
    are MAE-reasonable but MAPE-ugly (small-number MAPE distortion).
    All synthetic DataFrames -- no Postgres required."""

    def test_flags_series_that_are_mae_reasonable_but_mape_ugly(self):
        from models.forecasting.train_prophet import summarize_graduation_count_reconciliation

        report_df = pd.DataFrame(
            [
                # No champion, MAE=2 (<=3 default threshold), MAPE=33% (>25% default) -> flagged.
                _report_row(
                    "BSCS", "CICT", "graduation_count", prophet_mae=2.0, best_baseline_mae=1.5,
                    beats=False, prophet_mape=33.0,
                ),
                # No champion, but MAE=8 exceeds the threshold -> genuinely bad, not flagged.
                _report_row(
                    "BSIT", "CICT", "graduation_count", prophet_mae=8.0, best_baseline_mae=4.0,
                    beats=False, prophet_mape=40.0,
                ),
                # Champion series (beats baseline) -- excluded regardless of MAE/MAPE.
                _report_row(
                    "BSCE", "COE", "graduation_count", prophet_mae=1.0, best_baseline_mae=5.0,
                    beats=True, prophet_mape=50.0,
                ),
                # enrollment_count -- wrong metric, must never be considered.
                _report_row(
                    "BSCS", "CICT", "enrollment_count", prophet_mae=2.0, best_baseline_mae=1.5,
                    beats=False, prophet_mape=33.0,
                ),
            ]
        )
        result = summarize_graduation_count_reconciliation(report_df)

        # 2 graduation_count series had no champion (BSCS, BSIT); only
        # BSCS clears the "reasonable MAE, ugly MAPE" bar.
        assert result.total_no_champion == 2
        assert result.count == 1
        assert result.flagged[0].program_id == "BSCS"
        assert "1 of 2" in result.summary_line()

    def test_does_not_flag_when_mape_is_nan(self):
        """A NaN MAPE (every actual value was 0 across all folds -- see
        metrics.mape's documented undefined case) must never compare as
        \"greater than\" the threshold and get flagged; pandas' default
        NaN-comparison behavior already does the right thing here, but
        this test exists to keep that behavior from silently regressing."""
        from models.forecasting.train_prophet import summarize_graduation_count_reconciliation

        report_df = pd.DataFrame(
            [
                _report_row(
                    "BSCS", "CICT", "graduation_count", prophet_mae=1.0, best_baseline_mae=0.5,
                    beats=False, prophet_mape=float("nan"),
                ),
            ]
        )
        result = summarize_graduation_count_reconciliation(report_df)
        assert result.total_no_champion == 1
        assert result.count == 0

    def test_summary_line_handles_zero_no_champion_series(self):
        from models.forecasting.train_prophet import summarize_graduation_count_reconciliation

        report_df = pd.DataFrame(
            [
                _report_row(
                    "BSCE", "COE", "graduation_count", prophet_mae=1.0, best_baseline_mae=5.0,
                    beats=True, prophet_mape=10.0,
                ),
            ]
        )
        result = summarize_graduation_count_reconciliation(report_df)
        assert result.total_no_champion == 0
        assert result.count == 0
        assert "No graduation_count series were without a Prophet champion" in result.summary_line()

    def test_custom_thresholds_are_respected(self):
        """A caller with a different tolerance (e.g. a smaller institution
        where 3 students is already a big miss) can tighten the bar."""
        from models.forecasting.train_prophet import summarize_graduation_count_reconciliation

        report_df = pd.DataFrame(
            [
                _report_row(
                    "BSCS", "CICT", "graduation_count", prophet_mae=2.0, best_baseline_mae=1.5,
                    beats=False, prophet_mape=33.0,
                ),
            ]
        )
        # Default thresholds (mae<=3, mape>25) would flag this row.
        default_result = summarize_graduation_count_reconciliation(report_df)
        assert default_result.count == 1

        # Tighter MAE threshold excludes it.
        tightened = summarize_graduation_count_reconciliation(report_df, mae_threshold=1.0)
        assert tightened.count == 0


def _postgres_available() -> bool:
    from pipelines.common.postgres import get_admin_connection
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


# Scoped to THIS class only, not the module -- a bare module-level
# `pytestmark = pytest.mark.skipif(...)` silently applies to every test
# in the file regardless of where it's defined (Python builds the whole
# module, including this line, before pytest ever looks at it), which
# was previously skipping every no-Postgres-required test above too
# whenever Postgres wasn't reachable. Class-scoping keeps the skip
# confined to the tests that actually need a live database.
@pytest.mark.skipif(not _postgres_available(), reason="Requires a reachable Postgres instance")
class TestPostgresIntegration:
    @pytest.fixture(scope="class")
    def engine(self):
        from sqlalchemy import create_engine
        return create_engine(
            f"postgresql+psycopg2://pipeline_writer:{PIPELINE_WRITER_PASSWORD}@"
            f"{TEST_ENV['POSTGRES_HOST']}:{TEST_ENV['POSTGRES_PORT']}/{TEST_ENV['POSTGRES_DB']}"
        )

    @pytest.fixture(scope="class")
    def evaluation_report(self, engine):
        from models.forecasting.train_prophet import evaluate_all_series
        return evaluate_all_series(engine)

    @pytest.fixture(scope="class")
    def program_count(self, engine):
        """Actual gold.dim_program row count -- P1 fix asserts against this
        instead of a hardcoded number, since program count is a config
        value (configs/programs.yaml), not a project constant like the
        8 colleges were."""
        conn = engine.raw_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM gold.dim_program")
                return cur.fetchone()[0]
        finally:
            conn.close()

    def test_evaluation_report_has_at_most_one_row_per_program_per_metric(self, evaluation_report, program_count):
        # <=, not ==: programs below MIN_HISTORY_PERIODS are legitimately
        # skipped (see has_sufficient_history), so this is an upper bound,
        # not an exact-count assertion.
        assert len(evaluation_report) <= program_count * 2

    def test_evaluation_report_flags_series_where_prophet_does_not_beat_baseline(self, evaluation_report):
        assert "prophet_beats_best_baseline" in evaluation_report.columns
        assert set(evaluation_report["prophet_beats_best_baseline"].unique()) <= {True, False}

    def test_prophet_evaluation_report_has_program_and_college_columns(self, evaluation_report):
        # P1 fix: series are (program, metric) now, but college_id is still
        # carried through (denormalized) so results remain rollup-able.
        assert {"program_id", "college_id", "metric"} <= set(evaluation_report.columns)

    def test_train_final_models_saves_one_artifact_per_evaluated_series(self, engine, tmp_path, evaluation_report):
        from pathlib import Path
        from models.forecasting.train_prophet import train_final_models

        paths = train_final_models(engine, artifacts_dir=tmp_path)
        # <=, not ==, for the same has_sufficient_history reason as above.
        assert len(paths) <= len(evaluation_report)
        for p in paths:
            assert Path(p).exists()

    def test_write_evaluation_report_produces_both_csv_and_markdown(self, evaluation_report, tmp_path):
        from models.forecasting.train_prophet import write_evaluation_report

        csv_path, md_path = write_evaluation_report(evaluation_report, artifacts_dir=tmp_path)
        assert csv_path.exists()
        assert md_path.exists()
        assert "Prophet beats the best baseline" in md_path.read_text(encoding="utf-8")