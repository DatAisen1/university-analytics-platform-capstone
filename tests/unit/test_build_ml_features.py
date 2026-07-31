"""
tests/unit/test_build_ml_features.py

Tests for pipelines/gold/build_ml_features.py: no feature leakage, row
count matches expected entity x semester count, and hand-computed values
(verified manually against real output during development) locked in as
a regression test.
"""

import os

import pytest

from pipelines.common.postgres import get_admin_connection

TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}
PIPELINE_WRITER_PASSWORD = os.environ.get("TEST_PIPELINE_WRITER_PASSWORD", "pw_pipeline123")


def _postgres_available() -> bool:
    try:
        conn = get_admin_connection(TEST_ENV)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Requires a reachable Postgres instance")


def test_sql_references_both_target_metrics_and_join_to_dim_semester():
    from pipelines.gold.build_ml_features import build_ml_features_sql

    sql = build_ml_features_sql()
    assert "enrollment_count" in sql
    assert "graduation_count" in sql
    assert "JOIN gold.dim_semester" in sql


def test_sql_excludes_current_row_from_every_aggregate_window():
    from pipelines.gold.build_ml_features import build_ml_features_sql

    sql = build_ml_features_sql()
    assert sql.count("ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING") == 6
    assert sql.count("ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING") == 2


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    return create_engine(
        f"postgresql+psycopg2://pipeline_writer:{PIPELINE_WRITER_PASSWORD}@"
        f"{TEST_ENV['POSTGRES_HOST']}:{TEST_ENV['POSTGRES_PORT']}/{TEST_ENV['POSTGRES_DB']}"
    )


def test_feature_table_row_count_matches_college_times_semester_count(engine):
    from pipelines.gold.build_ml_features import build_ml_forecast_features

    df = build_ml_forecast_features(engine)
    assert len(df) == 64


def test_first_semester_has_no_leakage_possible(engine):
    from pipelines.gold.build_ml_features import build_ml_forecast_features

    df = build_ml_forecast_features(engine)
    first_semester_rows = df[df["semester_key"] == df.groupby("college_key")["semester_key"].transform("min")]

    feature_columns = [
        c for c in df.columns
        if any(c.endswith(suffix) for suffix in (
            "_lag_1", "_lag_2", "_rolling_avg_2", "_historical_avg", "_trend", "_seasonality", "_growth"
        ))
    ]
    assert len(feature_columns) > 0
    for col in feature_columns:
        assert first_semester_rows[col].isna().all(), f"{col} has a non-null value at the first semester -- leakage"


def test_hand_computed_values_for_college_1(engine):
    from pipelines.gold.build_ml_features import build_ml_forecast_features

    df = build_ml_forecast_features(engine)
    college_1 = df[df["college_key"] == 1].sort_values("semester_key").reset_index(drop=True)

    row3 = college_1.iloc[2]
    assert row3["enrollment_count"] == 169
    assert row3["enrollment_count_lag_1"] == 92
    assert row3["enrollment_count_lag_2"] == 94
    assert row3["enrollment_count_rolling_avg_2"] == pytest.approx(93.0)
    assert row3["enrollment_count_trend"] == pytest.approx(-2.0)
    assert row3["enrollment_count_seasonality"] == pytest.approx(94.0)
    assert row3["enrollment_count_growth"] == pytest.approx((92 - 94) / 94)

    row5 = college_1.iloc[4]
    assert row5["enrollment_count_seasonality"] == pytest.approx(131.5)
    assert row5["enrollment_count_historical_avg"] == pytest.approx(123.5)


def test_build_and_store_ml_features_writes_to_postgres(engine):
    from pipelines.gold.build_ml_features import build_and_store_ml_features

    row_count = build_and_store_ml_features(engine)
    assert row_count == 64

    with engine.connect() as conn:
        from sqlalchemy import text
        result = conn.execute(text("SELECT COUNT(*) FROM gold.ml_forecast_features")).scalar()
        assert result == 64
