"""
tests/unit/test_build_ml_features.py

Task 31/32/33 regression coverage for pipelines/gold/build_ml_features.py:

- Grain: output has one row per (college, program, period) actually
  present in the input -- never collapsed to fewer rows than that
  (Task 31's "don't accidentally aggregate everyone together").
- Leakage: a sentinel value planted at a FUTURE period must never
  appear in any lag/rolling/historical-average feature for an EARLIER
  period (Task 32).
- Reproducibility: running the same query against the same fixture data
  twice produces an identical fingerprint (Task 33).

Requires a Postgres instance (uses real window-function SQL against a
real, freshly migrated schema) via TEST_POSTGRES_* environment
variables. Skipped automatically if unavailable, matching the existing
pattern in tests/integration/test_database_constraints.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from pipelines.common.postgres import bootstrap_warehouse, get_admin_connection
from pipelines.gold.build_ml_features import (
    build_program_forecast_features,
    feature_dataset_fingerprint,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _pg_test_db import create_isolated_database, drop_database_if_exists  # noqa: E402

TEST_ENV = {
    "POSTGRES_HOST": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("TEST_POSTGRES_DB", "university_analytics"),
    "POSTGRES_USER": os.environ.get("TEST_POSTGRES_USER", "uap_admin"),
    "POSTGRES_PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "local_dev_password"),
}
ROLE_PASSWORDS = {
    "pipeline_writer": "pw_pipeline123",
    "dbt_role": "pw_dbt123",
    "dashboard_reader": "pw_dash123",
    "analyst_readonly": "pw_analyst123",
}

# P1 fix (architecture, not a typo): this fixture used to run
# `DROP SCHEMA ... CASCADE` directly against TEST_ENV["POSTGRES_DB"] --
# the same database name test_dbt_marts.py / test_train_prophet.py
# expect to already hold real, populated pipeline output. Dropping the
# whole schema there is the single most destructive possible action one
# test module could take against another module's required state. This
# module gets its own throwaway database instead (see
# tests/_pg_test_db.py), so its DROP SCHEMA can never touch anything but
# a database this module alone created and will delete when it's done.
_ISOLATED_DB_BASE = f"{TEST_ENV['POSTGRES_DB']}_ml_features_test"
ISOLATED_ENV: dict = {}


def _postgres_available() -> bool:
    try:
        get_admin_connection(TEST_ENV).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="No reachable Postgres instance -- see module docstring"
)


@pytest.fixture(scope="module", autouse=True)
def clean_database():
    global ISOLATED_ENV
    db_name = create_isolated_database(_ISOLATED_DB_BASE, TEST_ENV)
    ISOLATED_ENV = {**TEST_ENV, "POSTGRES_DB": db_name}
    bootstrap_warehouse(ROLE_PASSWORDS, env=ISOLATED_ENV)
    yield
    drop_database_if_exists(db_name, TEST_ENV)


@pytest.fixture
def engine():
    host, port, db = ISOLATED_ENV["POSTGRES_HOST"], ISOLATED_ENV["POSTGRES_PORT"], ISOLATED_ENV["POSTGRES_DB"]
    return create_engine(
        f"postgresql+psycopg2://pipeline_writer:{ROLE_PASSWORDS['pipeline_writer']}@{host}:{port}/{db}"
    )


@pytest.fixture
def seeded_fixture(engine):
    """One college, one program, four periods. Period 4's enrollment
    count (999) is a sentinel that must NEVER leak into period 1-3's
    features."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO gold.dim_college (college_key, college_id, college_name)
            VALUES (1, 'COE', 'College of Engineering')
        """))
        conn.execute(text("""
            INSERT INTO gold.dim_program (program_key, program_id, program_name, college_id,
                                           program_level, nominal_duration_years, college_key)
            VALUES (1, 'BSCE', 'BS Civil Engineering', 'COE', 'UNDERGRAD', 4.0, 1)
        """))
        for ordinal, (year, sem) in enumerate([(2022, 1), (2022, 2), (2023, 1), (2023, 2)], start=1):
            conn.execute(text("""
                INSERT INTO gold.dim_academic_period
                    (academic_period_key, academic_year, semester_number, year_label,
                     semester_label, period_label, period_ordinal)
                VALUES (:key, :year, :sem, :year_label, :sem_label, :period_label, :ordinal)
            """), {
                "key": ordinal, "year": year, "sem": sem,
                "year_label": str(year), "sem_label": f"S{sem}",
                "period_label": f"{year}-{sem}", "ordinal": ordinal,
            })

        counts = {1: 10, 2: 12, 3: 15, 4: 999}  # period 4 is the sentinel
        for period_key, count in counts.items():
            for i in range(count):
                conn.execute(text("""
                    INSERT INTO gold.dim_student (student_key, student_id, gender_key, birth_year,
                                                   home_province, admission_type, college_key, program_key,
                                                   _valid_from_period_key, _is_current)
                    VALUES (:sk, :sid, 1, 2000, 'Nueva Ecija', 'REGULAR', 1, 1, :pk, TRUE)
                """), {"sk": period_key * 10000 + i, "sid": f"S{period_key}-{i}", "pk": period_key})
                conn.execute(text("""
                    INSERT INTO gold.fact_enrollment (student_key, program_key, college_key,
                                                       academic_period_key, enrollment_status,
                                                       year_level_key, units_enrolled, is_new_enrollee)
                    VALUES (:sk, 1, 1, :pk, 'ENROLLED', 1, 18, FALSE)
                """), {"sk": period_key * 10000 + i, "pk": period_key})
    yield


def test_grain_is_college_program_period_not_collapsed(engine, seeded_fixture):
    df = build_program_forecast_features(engine)
    assert len(df) == 4, "expected one row per period at (college, program) grain, got a collapsed result"
    assert set(df["program_key"]) == {1}
    assert set(df["academic_period_key"]) == {1, 2, 3, 4}


def test_no_future_leakage_into_earlier_periods(engine, seeded_fixture):
    """The sentinel value (999) planted at period 4 must not appear in
    ANY lag/rolling/historical_avg/trend feature for periods 1-3."""
    df = build_program_forecast_features(engine).sort_values("period_ordinal")
    feature_cols = [c for c in df.columns if c.startswith("enrollment_count_")]

    earlier_periods = df[df["period_ordinal"] < 4]
    for col in feature_cols:
        assert not (earlier_periods[col] == 999).any(), (
            f"leakage detected: sentinel value from period 4 appeared in {col} "
            f"for an earlier period"
        )

    # Period 1 (the very first period) must have all history-dependent
    # features as NULL -- there IS no "before" for it, so any non-null
    # value there would mean the query looked past its own start.
    period_1 = df[df["period_ordinal"] == 1].iloc[0]
    for col in feature_cols:
        assert pd.isna(period_1[col]), f"{col} should be NULL for the first period, got {period_1[col]}"


def test_reproducible_across_runs(engine, seeded_fixture):
    """Task 33: identical input -> identical fingerprint, every run."""
    df_1 = build_program_forecast_features(engine)
    df_2 = build_program_forecast_features(engine)
    assert feature_dataset_fingerprint(df_1) == feature_dataset_fingerprint(df_2)