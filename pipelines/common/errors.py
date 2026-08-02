"""
pipelines/common/errors.py

Standardized, categorized pipeline errors (Task 46) and a traceable
failure formatter (Task 47).

Why one taxonomy instead of ad-hoc exceptions per module: before this,
every stage raised whatever exception type was locally convenient
(ValueError, KeyError, FileNotFoundError, or bespoke classes like
ConfigError / IngestionError / MissingTableError / MigrationError).
Orchestration (orchestration/assets.py) could only report `str(exc)` --
"Pipeline failed" -- with no reliable way to tell a bad MinIO connection
from a bad year_level from a Postgres constraint violation without
parsing message text.

Every raised pipeline error now carries:
  1. `category`   -- one of the 14 closed PipelineErrorCategory values.
  2. `stage`      -- human-readable stage name (e.g. "Silver Transformation").
  3. `rows_affected` -- optional row count, so failures are traceable to
                     "how much data", not just "something broke".
  4. `entity` / `details` -- free-form context (entity name, offending
                     column, DB error code, ...).

Existing bespoke exceptions (ConfigError, IngestionError,
MissingTableError, MigrationError, MigrationChecksumError) now subclass
the appropriate category here, so `except ConfigError:` call sites
elsewhere keep working unchanged, while new code -- and
orchestration/assets.py in particular -- can catch the single
PipelineError base class and get category/stage/rows_affected for free.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class PipelineErrorCategory(str, Enum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_ACADEMIC_YEAR = "INVALID_ACADEMIC_YEAR"
    INVALID_SEMESTER = "INVALID_SEMESTER"
    INVALID_YEAR_LEVEL = "INVALID_YEAR_LEVEL"
    DUPLICATE_DATA = "DUPLICATE_DATA"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    MINIO_ERROR = "MINIO_ERROR"
    DUCKDB_ERROR = "DUCKDB_ERROR"
    POSTGRES_ERROR = "POSTGRES_ERROR"
    DBT_ERROR = "DBT_ERROR"
    FEATURE_ENGINEERING_ERROR = "FEATURE_ENGINEERING_ERROR"
    MODEL_TRAINING_ERROR = "MODEL_TRAINING_ERROR"
    MODEL_EVALUATION_ERROR = "MODEL_EVALUATION_ERROR"
    FORECAST_ERROR = "FORECAST_ERROR"


class PipelineError(Exception):
    """Base class for every categorized pipeline failure. Subclasses set
    `category` as a class attribute so callers raise e.g.
    `InvalidYearLevelError(...)` without repeating `category=...` at
    every call site."""

    category: PipelineErrorCategory = PipelineErrorCategory.DATA_QUALITY_FAILURE

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        rows_affected: Optional[int] = None,
        entity: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        category: Optional[PipelineErrorCategory] = None,
    ) -> None:
        self.message = message
        self.stage = stage
        self.rows_affected = rows_affected
        self.entity = entity
        self.details = details or {}
        if category is not None:
            self.category = category
        super().__init__(self.to_report())

    def to_report(self) -> str:
        """Task 47's exact shape: Stage / Error / Rows affected, plus
        category and any extra context -- never just 'Pipeline failed'."""
        lines = [f"Stage: {self.stage}", f"Error: {self.message}"]
        if self.entity:
            lines.append(f"Entity: {self.entity}")
        if self.rows_affected is not None:
            lines.append(f"Rows affected: {self.rows_affected}")
        if self.details:
            lines.append("Details: " + ", ".join(f"{k}={v}" for k, v in self.details.items()))
        lines.append(f"Category: {self.category.value}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "category": self.category.value,
            "message": self.message,
            "rows_affected": self.rows_affected,
            "entity": self.entity,
            "details": self.details,
        }

    def __str__(self) -> str:  # noqa: D105
        return self.to_report()


class InvalidSchemaError(PipelineError):
    category = PipelineErrorCategory.INVALID_SCHEMA


class InvalidAcademicYearError(PipelineError):
    category = PipelineErrorCategory.INVALID_ACADEMIC_YEAR


class InvalidSemesterError(PipelineError):
    category = PipelineErrorCategory.INVALID_SEMESTER


class InvalidYearLevelError(PipelineError):
    category = PipelineErrorCategory.INVALID_YEAR_LEVEL


class DuplicateDataError(PipelineError):
    category = PipelineErrorCategory.DUPLICATE_DATA


class DataQualityFailureError(PipelineError):
    category = PipelineErrorCategory.DATA_QUALITY_FAILURE


class MinioError(PipelineError):
    category = PipelineErrorCategory.MINIO_ERROR


class DuckDBError(PipelineError):
    category = PipelineErrorCategory.DUCKDB_ERROR


class PostgresError(PipelineError):
    category = PipelineErrorCategory.POSTGRES_ERROR


class DbtError(PipelineError):
    category = PipelineErrorCategory.DBT_ERROR


class FeatureEngineeringError(PipelineError):
    category = PipelineErrorCategory.FEATURE_ENGINEERING_ERROR


class ModelTrainingError(PipelineError):
    category = PipelineErrorCategory.MODEL_TRAINING_ERROR


class ModelEvaluationError(PipelineError):
    category = PipelineErrorCategory.MODEL_EVALUATION_ERROR


class ForecastError(PipelineError):
    category = PipelineErrorCategory.FORECAST_ERROR


def classify_exception(exc: Exception, *, stage: str) -> PipelineError:
    """Fallback classifier for a third-party exception that never went
    through a PipelineError subclass (a bare psycopg2/duckdb/botocore/
    pandera error slipping past a lower-level `except`). Used by
    orchestration/assets.py so an unexpected failure still produces a
    traceable, categorized report instead of a bare 'Pipeline failed'.
    Best-effort: a genuinely unrecognized exception type falls back to
    DATA_QUALITY_FAILURE rather than raising a *new* error out of the
    error handler itself.
    """
    if isinstance(exc, PipelineError):
        return exc

    module_root = type(exc).__module__.split(".")[0]
    category_by_module = {
        "psycopg2": PipelineErrorCategory.POSTGRES_ERROR,
        "sqlalchemy": PipelineErrorCategory.POSTGRES_ERROR,
        "duckdb": PipelineErrorCategory.DUCKDB_ERROR,
        "botocore": PipelineErrorCategory.MINIO_ERROR,
        "boto3": PipelineErrorCategory.MINIO_ERROR,
        "pandera": PipelineErrorCategory.INVALID_SCHEMA,
    }
    category = category_by_module.get(module_root, PipelineErrorCategory.DATA_QUALITY_FAILURE)
    return PipelineError(
        str(exc), stage=stage, category=category,
        details={"exception_type": f"{type(exc).__module__}.{type(exc).__name__}"},
    )