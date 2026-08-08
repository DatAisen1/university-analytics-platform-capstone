"""
pipelines/common/settings.py

Centralized application configuration boundary (P0.12-P0.17):

    .env
     |
     v
    Pydantic Settings   (this module)
     |
     v
    Application Configuration   (PostgresSettings, MinioSettings, PipelineSettings,
     |                            DagsterSettings, DBTSettings, MLSettings)
     v
    Services   (pipelines.common.postgres, pipelines.common.storage,
                orchestration.assets, models.forecasting.*, scripts.*)

Before this module, POSTGRES_*/MINIO_*/PIPELINE_WRITER_PASSWORD etc. were
read ad hoc via `os.environ[...]` / `os.environ.get(...)` at more than a
dozen call sites (pipelines/common/postgres.py, pipelines/common/storage.py,
orchestration/assets.py, models/forecasting/*.py, pipelines/gold/*.py,
pipelines/silver/*.py, scripts/*.py), each with its own idea of which
variables were required and what a missing one should look like. A typo'd
variable name failed with a bare KeyError deep inside whichever function
first touched it, and "what does this app need to run" was only
answerable by grepping the whole tree (see docs/16_Module_Responsibility_Audit.md
and pipelines/common/storage.py's load_minio_storage_from_env docstring,
which flagged this exact gap).

This module is the single place that:

  1. Declares every configuration variable the app consumes, grouped into
     the domains the stabilization backlog specifies: PostgresSettings,
     MinioSettings, PipelineSettings, DagsterSettings, DBTSettings,
     MLSettings.
  2. Validates required variables are present and well-typed BEFORE any
     service code runs, raising a specific, human-readable SettingsError
     -- never a raw pydantic traceback or a KeyError several frames deep
     (P0.15).
  3. Reads from the process environment merged with `.env` (process env
     wins -- so `docker compose run -e FOO=bar` or a CI secret can
     override what's checked into .env.example without editing files),
     UNLESS a caller supplies an explicit `env` mapping, in which case
     that mapping is used exactly as given and the real process
     environment is never consulted. This second path is the existing
     pattern every test fixture in this repo already relies on
     (`env={...}`, `TEST_ENV`) -- preserving it is what keeps every
     existing test passing unchanged while still centralizing the
     production code path onto Pydantic validation.

Each domain class is also the shape contract for its slice of
.env.example: add a required field here and every consumer immediately
fails at startup with one clear SettingsError (P0.15) instead of
misbehaving quietly several stages downstream.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from pipelines.common.config import ConfigError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"

_T = TypeVar("_T", bound="_DomainSettings")


class SettingsError(ConfigError):
    """Raised when a configuration domain can't be built: a required
    variable is missing, or a present one has the wrong shape (e.g.
    POSTGRES_PORT='not-a-number'). Subclasses ConfigError -- which is
    itself an InvalidSchemaError (pipelines/common/errors.py) -- so
    existing `except ConfigError:` call sites keep working unchanged,
    and orchestration can still catch the single PipelineError base
    class and get category=INVALID_SCHEMA for free."""

    def __init__(self, message: str, *, stage: str = "Application Settings", **kwargs):
        super().__init__(message, stage=stage, **kwargs)


class _DomainSettings(BaseModel):
    """Base for every configuration domain below. `extra='ignore'`
    because each domain only cares about its own slice of the
    environment -- MinioSettings shouldn't reject a config source just
    because POSTGRES_* is also present in it."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _dotenv_values(path: Path) -> Dict[str, str]:
    """Parse `.env` into a plain dict without mutating os.environ (unlike
    python-dotenv's load_dotenv). A missing file returns {} -- .env is
    optional in CI/Docker/production, where real environment variables
    are already set some other way."""
    if not path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _process_env() -> Dict[str, str]:
    """`.env` values, overridden by whatever's actually set in the
    process environment -- matches the precedence a developer expects:
    exporting a variable in your shell always wins over a stale `.env`
    entry, and Docker Compose's `environment:` block always wins over
    `env_file:` for the same reason."""
    merged = _dotenv_values(_ENV_FILE)
    merged.update(os.environ)
    return merged


def _load(cls: Type[_T], env: Optional[Mapping[str, str]], *, purpose: str) -> _T:
    """Build and validate one configuration domain.

    `env=None` reads the real process environment (+ .env) -- the
    production/CLI code path. An explicit mapping (including `{}`) is
    used exactly as given, with no fallback to the real environment --
    the deterministic test-fixture code path every existing test in this
    repo already relies on (`env=TEST_ENV`, `env={}`).
    """
    source = env if env is not None else _process_env()
    data = {
        name: source[name]
        for name in cls.model_fields
        if name in source and source[name] not in (None, "")
    }
    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        missing = sorted({str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"})
        if missing:
            raise SettingsError(
                f"Missing required environment variable(s) for {purpose}: {missing}. "
                f"Run 'cp .env.example .env' and fill in real values, then re-run."
            ) from exc
        raise SettingsError(f"Invalid configuration for {purpose}: {exc}") from exc


# ---------------------------------------------------------------------------
# Configuration domains (P0.13)
# ---------------------------------------------------------------------------


class PostgresSettings(_DomainSettings):
    """The data warehouse connection + the four service-role passwords
    (see warehouse/ddl/002_grants.sql / docs/06_Data_Warehouse.md Section 5).
    POSTGRES_USER/PASSWORD are admin credentials -- required only for the
    admin connection path (require_admin_credentials()), not for a plain
    role connection, which only needs host/port/db."""

    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None
    PIPELINE_WRITER_PASSWORD: Optional[str] = None
    DBT_ROLE_PASSWORD: Optional[str] = None
    DASHBOARD_READER_PASSWORD: Optional[str] = None
    ANALYST_READONLY_PASSWORD: Optional[str] = None

    def require_admin_credentials(self) -> "PostgresSettings":
        """get_admin_connection() needs POSTGRES_USER/PASSWORD -- enforced
        here (not as a model-level requirement) because a plain role
        connection (get_role_connection) legitimately doesn't need them."""
        missing = [
            name
            for name, value in (
                ("POSTGRES_USER", self.POSTGRES_USER),
                ("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD),
            )
            if not value
        ]
        if missing:
            raise SettingsError(
                f"Missing required environment variable(s) for Postgres admin connection: {missing}"
            )
        return self

    def require_pipeline_writer_password(self) -> str:
        """PIPELINE_WRITER_PASSWORD is required wherever pipeline code
        writes as the pipeline_writer role (Gold/Silver loaders, ML
        feature build, Dagster's warehouse/features/training/evaluation/
        forecast assets, forecasting train/deploy scripts)."""
        if not self.PIPELINE_WRITER_PASSWORD:
            raise SettingsError(
                "Missing required environment variable(s) for pipeline_writer connection: "
                "['PIPELINE_WRITER_PASSWORD']"
            )
        return self.PIPELINE_WRITER_PASSWORD

    def service_role_passwords(self) -> Dict[str, str]:
        """Passwords keyed exactly as pipelines.common.postgres.SERVICE_ROLES
        and bootstrap_roles() expect."""
        return {
            "pipeline_writer": self.PIPELINE_WRITER_PASSWORD or "",
            "dbt_role": self.DBT_ROLE_PASSWORD or "",
            "dashboard_reader": self.DASHBOARD_READER_PASSWORD or "",
            "analyst_readonly": self.ANALYST_READONLY_PASSWORD or "",
        }


class MinioSettings(_DomainSettings):
    """S3-compatible object storage for Bronze/Silver/Gold Parquet files.
    Bucket names default to the conventional bronze/silver/gold used
    throughout warehouse/ and .env.example, so a deployment that doesn't
    override them doesn't have to repeat the obvious value."""

    MINIO_ENDPOINT: str
    MINIO_ROOT_USER: str
    MINIO_ROOT_PASSWORD: str
    MINIO_API_PORT: int = 9000
    MINIO_CONSOLE_PORT: int = 9001
    MINIO_BRONZE_BUCKET: str = "bronze"
    MINIO_SILVER_BUCKET: str = "silver"
    MINIO_GOLD_BUCKET: str = "gold"

    @property
    def endpoint_url(self) -> str:
        return self.MINIO_ENDPOINT if self.MINIO_ENDPOINT.startswith("http") else f"http://{self.MINIO_ENDPOINT}"

    def bucket_for(self, bucket_env_var: str) -> str:
        """Look up a bucket name by the same env-var-name string callers
        already pass around (e.g. 'MINIO_BRONZE_BUCKET') -- preserves
        load_minio_storage_from_env()'s existing call-site contract."""
        if bucket_env_var not in ("MINIO_BRONZE_BUCKET", "MINIO_SILVER_BUCKET", "MINIO_GOLD_BUCKET"):
            raise SettingsError(f"Unknown MinIO bucket variable: {bucket_env_var!r}")
        return getattr(self, bucket_env_var)


class PipelineSettings(_DomainSettings):
    """Cross-cutting pipeline runtime settings (Task 63's logging, and
    the dev/staging/prod environment marker)."""

    ENVIRONMENT: str = "dev"
    LOG_LEVEL: str = "INFO"


class DagsterSettings(_DomainSettings):
    """No Dagster-specific environment variables are required today --
    orchestration/assets.py and orchestration/definitions.py get
    everything they need from PostgresSettings via get_postgres_settings().
    Kept as its own domain (per the stabilization backlog's suggested
    structure) so the first variable Dagster actually needs (DAGSTER_HOME,
    a sensor poll interval, ...) has one obvious home instead of becoming
    a new scattered os.environ[] call."""

    DAGSTER_HOME: Optional[str] = None


class DBTSettings(_DomainSettings):
    """dbt reads its own Postgres credentials via dbt/profiles.yml's
    env_var() Jinja calls (POSTGRES_HOST/PORT/DB, DBT_ROLE_PASSWORD) --
    that is dbt's own, dbt-native configuration mechanism, not scattered
    os.environ[] access from this codebase's Python, so it is
    intentionally left as-is (see dbt/profiles.yml's header comment).
    This domain covers Python-side dbt invocation, e.g.
    pipelines/common/dbt_runner.py's --profiles-dir."""

    DBT_PROFILES_DIR: str = "dbt"


class MLSettings(_DomainSettings):
    """No ML-specific environment variables exist yet -- Prophet
    training/evaluation/forecast code in models/forecasting/ takes its
    inputs as explicit function arguments and DataFrame columns, not
    environment variables (see docs/20 ml assumptions.md). Kept as its
    own domain for the same reason as DagsterSettings: a single, obvious
    home for the first one that shows up."""


# ---------------------------------------------------------------------------
# Loaders -- the only functions in this module that touch `env`/os.environ
# directly. Every consumer below (postgres.py, storage.py, orchestration,
# models/forecasting/*, scripts/*) calls one of these instead of reading
# os.environ itself (P0.14).
# ---------------------------------------------------------------------------


def get_postgres_settings(env: Optional[Mapping[str, str]] = None) -> PostgresSettings:
    return _load(PostgresSettings, env, purpose="Postgres configuration")


def get_minio_settings(env: Optional[Mapping[str, str]] = None) -> MinioSettings:
    return _load(MinioSettings, env, purpose="MinIO configuration")


def get_pipeline_settings(env: Optional[Mapping[str, str]] = None) -> PipelineSettings:
    return _load(PipelineSettings, env, purpose="pipeline runtime configuration")


def get_dagster_settings(env: Optional[Mapping[str, str]] = None) -> DagsterSettings:
    return _load(DagsterSettings, env, purpose="Dagster configuration")


def get_dbt_settings(env: Optional[Mapping[str, str]] = None) -> DBTSettings:
    return _load(DBTSettings, env, purpose="dbt configuration")


def get_ml_settings(env: Optional[Mapping[str, str]] = None) -> MLSettings:
    return _load(MLSettings, env, purpose="ML configuration")