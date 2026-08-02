"""
tests/unit/test_train_prophet.py

Tests for models/forecasting/train_prophet.py: the pure date-mapping
function, walk-forward fold structure, and (skipped if Postgres
unreachable) full integration tests against the live warehouse --
including the real, honest finding from Day 20's evaluation run locked
in as a regression test: Prophet beats baseline on enrollment_count
universally and loses on graduation_count universally, tracing directly
to the cohort-truncation limitation disclosed since Week 1
(docs/08_Faker_Data_Generator.md Section 10) -- graduation_count is 0
for 7 of 8 semesters for most colleges, making a naive "predict 0"
baseline structurally hard to beat.
"""

import os

import pytest


from models.forecasting.train_prophet import TEST_PERIOD_ORDINALS, semester_to_date
TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}
PIPELINE_WRITER_PASSWORD = os.environ.get("TEST_PIPELINE_WRITER_PASSWORD", "pw_pipeline123")


def test_semester_to_date_semester_1_is_january():
    assert semester_to_date(2021, 1) == "2021-01-01"


def test_semester_to_date_semester_2_is_july():
    assert semester_to_date(2021, 2) == "2021-07-01"


def test_semester_to_date_matches_dim_calendar_convention():
    assert semester_to_date(2024, 1)[:4] == "2024"
    assert semester_to_date(2024, 2)[:4] == "2024"


def test_exactly_four_walk_forward_test_points():
    assert TEST_PERIOD_ORDINALS == [5, 6, 7, 8]


def _postgres_available() -> bool:
    from pipelines.common.postgres import get_admin_connection
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Requires a reachable Postgres instance")


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    return create_engine(
        f"postgresql+psycopg2://pipeline_writer:{PIPELINE_WRITER_PASSWORD}@"
        f"{TEST_ENV['POSTGRES_HOST']}:{TEST_ENV['POSTGRES_PORT']}/{TEST_ENV['POSTGRES_DB']}"
    )


@pytest.fixture(scope="module")
def evaluation_report(engine):
    from models.forecasting.train_prophet import evaluate_all_series
    return evaluate_all_series(engine)


def test_evaluation_report_has_one_row_per_college_per_metric(evaluation_report):
    assert len(evaluation_report) == 16


def test_evaluation_report_flags_series_where_prophet_does_not_beat_baseline(evaluation_report):
    assert "prophet_beats_best_baseline" in evaluation_report.columns
    assert set(evaluation_report["prophet_beats_best_baseline"].unique()) == {True, False}


def test_prophet_beats_baseline_on_every_enrollment_series(evaluation_report):
    enrollment_rows = evaluation_report[evaluation_report["metric"] == "enrollment_count"]
    assert len(enrollment_rows) == 8
    assert enrollment_rows["prophet_beats_best_baseline"].all()


def test_prophet_does_not_beat_baseline_on_graduation_series(evaluation_report):
    graduation_rows = evaluation_report[evaluation_report["metric"] == "graduation_count"]
    assert len(graduation_rows) == 8
    assert not graduation_rows["prophet_beats_best_baseline"].any()


def test_train_final_models_saves_one_artifact_per_college_per_metric(engine, tmp_path):
    from pathlib import Path
    from models.forecasting.train_prophet import train_final_models

    paths = train_final_models(engine, artifacts_dir=tmp_path)
    assert len(paths) == 16
    for p in paths:
        assert Path(p).exists()


def test_write_evaluation_report_produces_both_csv_and_markdown(evaluation_report, tmp_path):
    from models.forecasting.train_prophet import write_evaluation_report

    csv_path, md_path = write_evaluation_report(evaluation_report, artifacts_dir=tmp_path)
    assert csv_path.exists()
    assert md_path.exists()
    assert "Prophet beats the best baseline" in md_path.read_text()
