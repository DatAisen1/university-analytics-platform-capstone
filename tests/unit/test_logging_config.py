"""
tests/unit/test_logging_config.py

Tests for pipelines/common/logging_config.py (Task 63) -- verifies that
PipelineStageLogger emits exactly one STARTED line and one terminal
SUCCESS/FAILED line, that the JSON payload carries every field Task 63
requires (run_id, stage, academic_year, semester, rows_processed,
rows_rejected, duration_ms, status), and that a failing block logs
FAILED without swallowing the exception.
"""

from __future__ import annotations

import json
import logging

import pytest

from pipelines.common.logging_config import (
    JSONFormatter,
    PipelineStageLogger,
    _ContextFilter,
    parse_partition_key,
)


def _capture_json_lines(logger_name: str = "pipeline") -> tuple[logging.Logger, list, logging.Handler]:
    """Attach an in-memory handler to the real 'pipeline' logger so we can
    assert on emitted records without touching the filesystem or relying
    on configure_logging()'s (deliberately idempotent, only-once) handler
    setup.

    Filters run per-handler in stdlib logging (not per-logger), so this
    handler needs its own _ContextFilter instance -- same as the real
    console/file handlers configure_logging() attaches -- or run_id/
    academic_year/semester never get set on the record at all.
    """
    records: list = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(json.loads(JSONFormatter().format(record)))

    logger = logging.getLogger(logger_name)
    handler = _ListHandler()
    handler.addFilter(_ContextFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger, records, handler


def test_parse_partition_key_extracts_year_and_semester():
    year, semester = parse_partition_key("academic_year=2024/semester=1st Semester")
    assert year == 2024
    assert semester == "1st Semester"


def test_parse_partition_key_handles_all_partition():
    year, semester = parse_partition_key("all")
    assert year is None
    assert semester is None


def test_stage_logger_emits_started_and_success_with_expected_fields():
    _, records, handler = _capture_json_lines()
    try:
        with PipelineStageLogger(
            run_id="run-abc",
            stage="silver",
            entity="enrollment",
            partition_key="academic_year=2024/semester=1st Semester",
        ) as stage_log:
            stage_log.rows_processed = 9850
            stage_log.rows_rejected = 150

        assert len(records) == 2
        started, finished = records

        assert started["status"] == "STARTED"
        assert started["stage"] == "silver"
        assert started["run_id"] == "run-abc"

        assert finished["status"] == "SUCCESS"
        assert finished["run_id"] == "run-abc"
        assert finished["stage"] == "silver"
        assert finished["entity"] == "enrollment"
        assert finished["academic_year"] == 2024
        assert finished["semester"] == "1st Semester"
        assert finished["rows_processed"] == 9850
        assert finished["rows_rejected"] == 150
        assert isinstance(finished["duration_ms"], (int, float))
    finally:
        logging.getLogger("pipeline").removeHandler(handler)


def test_stage_logger_logs_failed_and_reraises_on_exception():
    _, records, handler = _capture_json_lines()
    try:
        with pytest.raises(ValueError, match="boom"):
            with PipelineStageLogger(run_id="run-xyz", stage="gold"):
                raise ValueError("boom")

        assert len(records) == 2
        started, finished = records
        assert started["status"] == "STARTED"
        assert finished["status"] == "FAILED"
        assert finished["run_id"] == "run-xyz"
        assert "exception" in finished
    finally:
        logging.getLogger("pipeline").removeHandler(handler)