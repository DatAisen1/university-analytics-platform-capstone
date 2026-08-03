"""
pipelines/common/logging_config.py

Structured, correlated logging for the pipeline (Task 63).

Why this exists SEPARATELY from pipelines.common.metadata:
metadata.py is the system of record for idempotency checks and queries
("show me every run that quarantined >5% of rows") -- it lives in DuckDB
and is queried with SQL after the fact. This module is for observability
while a run is happening: something a human can `tail -f logs/pipeline.log`
during a demo, or that a log shipper (CloudWatch, Datadog, ELK) can ingest
in production without a bespoke parser. They are complementary, not
redundant, and a run should always produce both.

Design decisions:
  1. One JSON object per line (JSONFormatter). Flat, not nested, so it
     loads straight into a dataframe (`pd.read_json(path, lines=True)`)
     or a log aggregator with zero custom mapping.
  2. run_id / academic_year / semester are bound ONCE per stage via
     contextvars (bind_run_context), not threaded as extra parameters
     through every function signature in the codebase. This keeps
     `ingest_one`, `_validate_file_level`, etc. free of logging-only
     parameters that have nothing to do with their actual job.
  3. PipelineStageLogger is a context manager: one `with` block =
     exactly one STARTED line and exactly one terminal SUCCESS/FAILED
     line carrying every field Task 63 asks for -- run_id, stage,
     academic_year, semester, rows_processed, rows_rejected,
     duration_ms, status. It NEVER swallows an exception; it logs FAILED
     and lets it propagate, same contract as a `try/finally`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = _REPO_ROOT / "logs"
LOGGER_NAME = "pipeline"

# ---------------------------------------------------------------------------
# Correlation context: set once per stage, read automatically by every log
# call made anywhere underneath it on the same async/thread context.
# ---------------------------------------------------------------------------
_run_id_var: ContextVar[str] = ContextVar("run_id", default="")
_academic_year_var: ContextVar[Optional[int]] = ContextVar("academic_year", default=None)
_semester_var: ContextVar[Optional[str]] = ContextVar("semester", default=None)


class _ContextFilter(logging.Filter):
    """Injects the current run_id/academic_year/semester into every
    LogRecord. Defaults keep the JSON shape stable even for log lines
    emitted outside any bind_run_context() block (e.g. a module import
    warning)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get() or "-"
        record.academic_year = _academic_year_var.get()
        record.semester = _semester_var.get()
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line for the file handler. Structured fields
    passed via `extra={"pipeline_extra": {...}}` (stage, status,
    rows_processed, rows_rejected, duration_ms, ...) ride along
    untouched -- this formatter never inspects log *message* text to
    recover structure, which is the usual way ad-hoc log-parsing rots."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "-"),
            "academic_year": getattr(record, "academic_year", None),
            "semester": getattr(record, "semester", None),
        }
        payload.update(getattr(record, "pipeline_extra", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(log_dir: Path = DEFAULT_LOG_DIR, level: int = logging.INFO) -> None:
    """Idempotent logging setup -- safe to call from every module that
    wants a logger (each does, via get_logger). Guards against the
    classic bug where a reloaded module (Dagster reloads on every run)
    re-attaches handlers and every line gets logged twice, then four
    times, then eight."""
    global _configured
    if _configured:
        return

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # don't also hand records to the root logger

    log_dir.mkdir(parents=True, exist_ok=True)
    context_filter = _ContextFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] run=%(run_id)s %(name)s: %(message)s"
    ))
    console_handler.addFilter(context_filter)

    # Rotating so a long-lived scheduler process doesn't grow
    # logs/pipeline.log without bound. 10MB x 5 backups is generous for a
    # batch pipeline logging once per stage per run, not once per row.
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "pipeline.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.addFilter(context_filter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Entry point every pipeline module should use instead of `print`:
    `logger = get_logger(__name__)`."""
    configure_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


@contextmanager
def bind_run_context(
    run_id: str, academic_year: Optional[int] = None, semester: Optional[str] = None
) -> Iterator[None]:
    """Bind correlation IDs for the duration of a `with` block. Nested
    calls -- ingest_all() -> ingest_one() -> _validate_file_level() --
    all pick up the same run_id automatically via the context var, with
    no parameter threading required and no risk of one call site
    forgetting to pass it along."""
    run_token = _run_id_var.set(run_id)
    year_token = _academic_year_var.set(academic_year)
    sem_token = _semester_var.set(semester)
    try:
        yield
    finally:
        _run_id_var.reset(run_token)
        _academic_year_var.reset(year_token)
        _semester_var.reset(sem_token)


def parse_partition_key(partition_key: str) -> Tuple[Optional[int], Optional[str]]:
    """Best-effort parse of this project's
    'academic_year=2024/semester=1st Semester' partition_key convention
    (see pipelines/ingestion/ingest_to_bronze.py) into (year, semester)
    for log context. Returns (None, None) for the 'all' partition_key
    used by non-semester-scoped entities (student, college, program)."""
    year: Optional[int] = None
    semester: Optional[str] = None
    for part in partition_key.split("/"):
        if part.startswith("academic_year="):
            raw = part.split("=", 1)[1]
            year = int(raw) if raw.isdigit() else None
        elif part.startswith("semester="):
            semester = part.split("=", 1)[1]
    return year, semester


class PipelineStageLogger:
    """One `with` block per (stage[, entity, partition_key]) = exactly
    one STARTED line and exactly one terminal SUCCESS/FAILED line.

    Usage:
        stage_log = PipelineStageLogger(
            run_id, stage="silver", entity="enrollment",
            partition_key="academic_year=2024/semester=1st Semester",
        )
        with stage_log:
            rows_in, rows_out = process_enrollment(...)
            stage_log.rows_processed = rows_out
            stage_log.rows_rejected = rows_in - rows_out
        # SUCCESS line is emitted automatically on clean exit.
        # If the block raises, a FAILED line (with exc_info) is emitted
        # and the exception is re-raised UNCHANGED -- this wrapper adds
        # observability, it never changes control flow or error handling.
    """

    def __init__(
        self,
        run_id: str,
        stage: str,
        entity: str = "",
        partition_key: str = "",
    ) -> None:
        self.run_id = run_id
        self.stage = stage
        self.entity = entity
        self.partition_key = partition_key
        self.rows_processed = 0
        self.rows_rejected = 0
        self._logger = get_logger(stage)
        self._start = 0.0
        self._year, self._semester = (
            parse_partition_key(partition_key) if partition_key else (None, None)
        )

    def __enter__(self) -> "PipelineStageLogger":
        self._start = time.monotonic()
        with bind_run_context(self.run_id, self._year, self._semester):
            self._logger.info(
                "%s started",
                self.stage,
                extra={"pipeline_extra": {
                    "stage": self.stage,
                    "entity": self.entity,
                    "partition_key": self.partition_key,
                    "status": "STARTED",
                }},
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = round((time.monotonic() - self._start) * 1000, 1)
        status = "FAILED" if exc_type else "SUCCESS"
        level = logging.ERROR if exc_type else logging.INFO
        with bind_run_context(self.run_id, self._year, self._semester):
            self._logger.log(
                level,
                "%s %s in %sms",
                self.stage, status.lower(), duration_ms,
                exc_info=exc is not None,
                extra={"pipeline_extra": {
                    "stage": self.stage,
                    "entity": self.entity,
                    "partition_key": self.partition_key,
                    "status": status,
                    "rows_processed": self.rows_processed,
                    "rows_rejected": self.rows_rejected,
                    "duration_ms": duration_ms,
                }},
            )
        return False  # never swallow the exception