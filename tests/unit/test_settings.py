"""
tests/unit/test_settings.py

Unit tests for pipelines/common/settings.py.

Coverage philosophy (matches tests/unit/test_config.py's approach): a
settings loader's job is failing fast with a specific, actionable error
when required configuration is missing, so the failure paths get equal
weight to the happy path -- not just "valid env builds an object."

Two isolation mechanisms are exercised throughout:
  1. `env={...}` (including `env={}`) is used EXACTLY as given, with no
     fallback to the real process environment or `.env` -- this is what
     lets these tests run deterministically regardless of what's actually
     exported in the shell that invokes pytest, and it's the same pattern
     tests/unit/test_postgres_access.py's TEST_ENV and
     tests/unit/test_storage.py's `env=` already rely on.
  2. `env=None` (the production/CLI default) reads the real process
     environment merged with `.env` -- exercised via monkeypatch.setenv,
     never by mutating a real `.env` file.
"""

from pathlib import Path

import pytest

from pipelines.common.config import ConfigError
from pipelines.common.settings import (
    DagsterSettings,
    DBTSettings,
    MinioSettings,
    MLSettings,
    PipelineSettings,
    PostgresSettings,
    SettingsError,
    get_dagster_settings,
    get_dbt_settings,
    get_minio_settings,
    get_ml_settings,
    get_pipeline_settings,
    get_postgres_settings,
)

VALID_POSTGRES_ENV = {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "university_analytics",
    "POSTGRES_USER": "uap_admin",
    "POSTGRES_PASSWORD": "local_dev_password",
    "PIPELINE_WRITER_PASSWORD": "pw_pipeline",
    "DBT_ROLE_PASSWORD": "pw_dbt",
    "DASHBOARD_READER_PASSWORD": "pw_dash",
    "ANALYST_READONLY_PASSWORD": "pw_analyst",
}

VALID_MINIO_ENV = {
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ROOT_USER": "uap_minio_admin",
    "MINIO_ROOT_PASSWORD": "minio_secret",
}


# ---------------------------------------------------------------------------
# SettingsError is a ConfigError (existing `except ConfigError:` call sites,
# and existing tests asserting ConfigError, keep working unchanged)
# ---------------------------------------------------------------------------


def test_settings_error_is_a_config_error():
    assert issubclass(SettingsError, ConfigError)


# ---------------------------------------------------------------------------
# PostgresSettings
# ---------------------------------------------------------------------------


def test_postgres_settings_happy_path():
    settings = get_postgres_settings(env=VALID_POSTGRES_ENV)
    assert settings.POSTGRES_HOST == "localhost"
    assert settings.POSTGRES_PORT == 5432  # coerced to int
    assert settings.POSTGRES_DB == "university_analytics"
    assert settings.service_role_passwords() == {
        "pipeline_writer": "pw_pipeline",
        "dbt_role": "pw_dbt",
        "dashboard_reader": "pw_dash",
        "analyst_readonly": "pw_analyst",
    }


def test_postgres_settings_missing_required_var_raises_human_readable_error():
    incomplete = dict(VALID_POSTGRES_ENV)
    del incomplete["POSTGRES_HOST"]
    with pytest.raises(ConfigError, match="Missing required environment variable.*POSTGRES_HOST"):
        get_postgres_settings(env=incomplete)


def test_postgres_settings_empty_env_raises_for_host_and_db():
    with pytest.raises(ConfigError, match="Missing required environment variable"):
        get_postgres_settings(env={})


def test_postgres_settings_invalid_port_raises_human_readable_error():
    bad = dict(VALID_POSTGRES_ENV)
    bad["POSTGRES_PORT"] = "not-a-number"
    with pytest.raises(ConfigError, match="Invalid configuration"):
        get_postgres_settings(env=bad)


def test_postgres_port_defaults_when_absent():
    minimal = {"POSTGRES_HOST": "localhost", "POSTGRES_DB": "university_analytics"}
    settings = get_postgres_settings(env=minimal)
    assert settings.POSTGRES_PORT == 5432


def test_require_admin_credentials_passes_with_user_and_password():
    settings = get_postgres_settings(env=VALID_POSTGRES_ENV)
    assert settings.require_admin_credentials() is settings


def test_require_admin_credentials_fails_without_user_and_password():
    """A role connection (get_role_connection) legitimately doesn't need
    POSTGRES_USER/PASSWORD -- only the admin path does, so this is
    enforced by require_admin_credentials(), not by the base schema."""
    role_only_env = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "university_analytics",
    }
    settings = get_postgres_settings(env=role_only_env)
    with pytest.raises(ConfigError, match="Postgres admin connection"):
        settings.require_admin_credentials()


def test_require_pipeline_writer_password_missing_raises():
    env = dict(VALID_POSTGRES_ENV)
    del env["PIPELINE_WRITER_PASSWORD"]
    settings = get_postgres_settings(env=env)
    with pytest.raises(ConfigError, match="PIPELINE_WRITER_PASSWORD"):
        settings.require_pipeline_writer_password()


def test_require_pipeline_writer_password_present_returns_it():
    settings = get_postgres_settings(env=VALID_POSTGRES_ENV)
    assert settings.require_pipeline_writer_password() == "pw_pipeline"


# ---------------------------------------------------------------------------
# MinioSettings
# ---------------------------------------------------------------------------


def test_minio_settings_happy_path_and_bucket_defaults():
    settings = get_minio_settings(env=VALID_MINIO_ENV)
    assert settings.MINIO_ROOT_USER == "uap_minio_admin"
    # Bucket names default to the conventional bronze/silver/gold when the
    # caller's env doesn't override them.
    assert settings.MINIO_BRONZE_BUCKET == "bronze"
    assert settings.MINIO_SILVER_BUCKET == "silver"
    assert settings.MINIO_GOLD_BUCKET == "gold"


def test_minio_settings_missing_required_var_raises():
    with pytest.raises(ConfigError, match="Missing required environment variable"):
        get_minio_settings(env={})


def test_minio_endpoint_url_adds_scheme_when_absent():
    settings = get_minio_settings(env=VALID_MINIO_ENV)
    assert settings.endpoint_url == "http://localhost:9000"


def test_minio_endpoint_url_preserves_existing_scheme():
    env = dict(VALID_MINIO_ENV, MINIO_ENDPOINT="https://minio.internal:9000")
    settings = get_minio_settings(env=env)
    assert settings.endpoint_url == "https://minio.internal:9000"


def test_minio_bucket_for_looks_up_by_env_var_name():
    env = dict(VALID_MINIO_ENV, MINIO_SILVER_BUCKET="custom-silver")
    settings = get_minio_settings(env=env)
    assert settings.bucket_for("MINIO_SILVER_BUCKET") == "custom-silver"


def test_minio_bucket_for_rejects_unknown_variable():
    settings = get_minio_settings(env=VALID_MINIO_ENV)
    with pytest.raises(ConfigError, match="Unknown MinIO bucket variable"):
        settings.bucket_for("MINIO_NOT_A_REAL_BUCKET")


# ---------------------------------------------------------------------------
# PipelineSettings / DagsterSettings / DBTSettings / MLSettings -- every
# field has a default, so an empty env is valid (nothing is "required at
# startup" for these domains today; see each class's docstring).
# ---------------------------------------------------------------------------


def test_pipeline_settings_defaults():
    settings = get_pipeline_settings(env={})
    assert settings.ENVIRONMENT == "dev"
    assert settings.LOG_LEVEL == "INFO"


def test_pipeline_settings_overrides():
    settings = get_pipeline_settings(env={"ENVIRONMENT": "prod", "LOG_LEVEL": "WARNING"})
    assert settings.ENVIRONMENT == "prod"
    assert settings.LOG_LEVEL == "WARNING"


def test_dagster_settings_defaults():
    assert get_dagster_settings(env={}) == DagsterSettings()


def test_dbt_settings_defaults():
    settings = get_dbt_settings(env={})
    assert settings.DBT_PROFILES_DIR == "dbt"


def test_ml_settings_defaults():
    assert get_ml_settings(env={}) == MLSettings()


# ---------------------------------------------------------------------------
# env=None reads the real process environment (+ .env), and an explicit
# mapping is never contaminated by it -- the isolation guarantee every
# other test in this module (and every existing test elsewhere that passes
# `env=TEST_ENV`) depends on.
# ---------------------------------------------------------------------------


def test_env_none_reads_process_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "env-var-host")
    monkeypatch.setenv("POSTGRES_DB", "env-var-db")
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    # Route .env resolution to a directory with no .env file so this test
    # is not sensitive to whatever real .env the developer happens to have.
    monkeypatch.setattr("pipelines.common.settings._ENV_FILE", Path("/nonexistent/.env"))
    settings = get_postgres_settings()
    assert settings.POSTGRES_HOST == "env-var-host"
    assert settings.POSTGRES_DB == "env-var-db"


def test_explicit_env_mapping_ignores_process_environment(monkeypatch):
    """A real POSTGRES_HOST exported in the shell must NOT leak into a
    call that explicitly passes its own `env=` mapping -- this is the
    exact isolation tests/unit/test_postgres_access.py's TEST_ENV and
    tests/unit/test_storage.py's `env=` already depend on."""
    monkeypatch.setenv("POSTGRES_HOST", "should-not-be-used")
    settings = get_postgres_settings(env=VALID_POSTGRES_ENV)
    assert settings.POSTGRES_HOST == "localhost"