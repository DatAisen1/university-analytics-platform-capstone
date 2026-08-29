"""
pipelines/silver/validate_and_dedupe.py

The second Silver stage (Day 11), run after cleaning (Day 10): dedupe
enrollment records to one row per (student_id, academic_year,
semester_number), then apply business-rule checks that quarantine rather
than reject silently.

ORDER MATTERS: dedup runs BEFORE business-rule checks, not after. A late
correction's whole point is that the newer version is the one that should
be trusted -- checking a since-superseded earlier version for correctness
and quarantining it would be checking the wrong thing. Production logic
should validate the CURRENT record, not stale prior versions of it.

Business rules implemented here (see docs/05_Medallion_Architecture.md):
  1. enrollment_status must be a recognized controlled-vocabulary value
     (not 'UNKNOWN:...', which Day 10's safe cleaner tags rather than
     rejects -- this is where that tag finally gets acted on).
  2. An enrollment record's (academic_year, semester_number) must fall
     within the student's valid observed range: not before their cohort
     entry semester, not after the observed window's end (see
     pipelines.common.academic_periods.OBSERVED_END_YEAR).
  3. Cross-entity consistency: a DROPPED enrollment record must have a
     matching dropout event, and vice versa; same for GRADUATED/graduation.
  4. Year-level progression must be mechanically plausible across a
     student's consecutive observed semesters.

A note on what to expect from the real dataset: because the synthetic
generator (Days 4-6) constructs enrollment/dropout/graduation records
together and correctly by design, rules 2 and 3 should quarantine ~0 rows
on the real data -- that's confirmation the earlier stages were built
correctly, not a sign the checks are pointless. Their value is proven by
unit/integration tests with deliberately injected bad rows (this day's
testing checklist), not by counting on real violations that a correct
generator won't produce.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import duckdb
import pandas as pd

from pipelines.common.academic_periods import OBSERVED_MAX_PERIOD_ORDINAL, OBSERVED_START_YEAR
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import ObjectStorage, load_storage_from_env
from pipelines.silver.progression_validation import check_year_level_progression

from pipelines.common.errors import DataQualityFailureError, DuplicateDataError

MAX_QUARANTINE_RATE = 0.25  # same tolerance as business_rules.py

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"

STAGE = "silver_validate_dedupe"

# P0 (Dataset Extension) fix: previously hardcoded as
# `(2024 - 2021) * 2 + 1` and `(academic_year - 2021)` below -- an
# independently-duplicated, stale copy of the same 2021/2024 year
# literals ingest_to_bronze.py's OBSERVED_SEMESTERS had (see that file's
# comment for the pre-P0.4 8-semester-model origin). This copy was more
# dangerous than that one: it doesn't fail soft. check_semester_within_
# cohort_range below QUARANTINES any row outside this bound, so once the
# observed window is extended (2021-2025, this task), a stale max here
# would have silently quarantined every real 2024/2025 enrollment record
# as "outside the student's valid cohort range" -- a correctness bug
# that would have surfaced as a mysterious mass-quarantine, not a crash.
# Both values now come from the canonical source.
OBSERVED_SEMESTERS_ORDINAL_MAX = OBSERVED_MAX_PERIOD_ORDINAL


def _semester_ordinal(academic_year: int, semester_number: int) -> int:
    return (academic_year - OBSERVED_START_YEAR) * 2 + (semester_number - 1)


def dedupe_enrollment(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> Tuple[pd.DataFrame, int]:
    """Keep exactly one row per (student_id, academic_year,
    semester_number): the one with the latest _ingested_at (last-write-
    wins). Returns (deduped_df, rows_dropped_count)."""
    conn.register("enrollment_view", df)
    result = conn.execute(
        """
        SELECT * EXCLUDE(_rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY student_id, academic_year, semester_number
                ORDER BY _ingested_at DESC
            ) AS _rn
            FROM enrollment_view
        )
        WHERE _rn = 1
        """
    ).df()
    conn.unregister("enrollment_view")
    return result, len(df) - len(result)


def check_known_status(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Quarantine any row whose enrollment_status is still tagged
    'UNKNOWN:...' -- Day 10's cleaning couldn't map it, and Day 11 is
    where that gets acted on rather than silently passed downstream."""
    is_unknown = df["enrollment_status"].astype(str).str.startswith("UNKNOWN:")
    valid = df[~is_unknown].copy()
    quarantined = df[is_unknown].copy()
    quarantined["_quarantine_reason"] = "unrecognized enrollment_status: " + quarantined["enrollment_status"]
    return valid, quarantined


def check_semester_within_cohort_range(
    enrollment_df: pd.DataFrame, student_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Quarantine enrollment records dated before the student's cohort
    entry semester, or after the observed window's end (see
    OBSERVED_SEMESTERS_ORDINAL_MAX above) -- a record outside that range
    cannot legitimately describe this student."""
    cohort_by_student = student_df.set_index("student_id")["cohort_academic_year"].to_dict()

    def _is_valid(row) -> bool:
        cohort_year = cohort_by_student.get(row["student_id"])
        if cohort_year is None:
            return False  # student_id not found in Silver student at all -- also invalid
        ordinal = _semester_ordinal(row["academic_year"], row["semester_number"])
        entry_ordinal = _semester_ordinal(int(cohort_year), 1)
        return entry_ordinal <= ordinal <= OBSERVED_SEMESTERS_ORDINAL_MAX

    mask = enrollment_df.apply(_is_valid, axis=1)
    valid = enrollment_df[mask].copy()
    quarantined = enrollment_df[~mask].copy()
    quarantined["_quarantine_reason"] = "academic_year/semester_number outside student's valid cohort range"
    return valid, quarantined


def check_dropout_consistency(
    enrollment_df: pd.DataFrame, dropout_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Cross-entity consistency: a DROPPED enrollment row must have a
    matching dropout event for the same (student_id, academic_year,
    semester_number), and a row with such a matching dropout event must
    itself be marked DROPPED. Either direction of mismatch is quarantined."""
    dropout_keys = set(
        zip(dropout_df["student_id"], dropout_df["academic_year"], dropout_df["semester_number"])
    )

    def _is_consistent(row) -> bool:
        key = (row["student_id"], row["academic_year"], row["semester_number"])
        has_dropout_event = key in dropout_keys
        is_marked_dropped = row["enrollment_status"] == "DROPPED"
        return has_dropout_event == is_marked_dropped

    mask = enrollment_df.apply(_is_consistent, axis=1)
    valid = enrollment_df[mask].copy()
    quarantined = enrollment_df[~mask].copy()
    quarantined["_quarantine_reason"] = "enrollment_status inconsistent with dropout event presence"
    return valid, quarantined


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def process_enrollment(
    silver_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
) -> Dict[str, object]:
    """Run the full Day 11 pipeline on enrollment: dedupe, then apply all
    three business rules in sequence, writing the final valid Silver
    enrollment table and a combined quarantine table.
    """
    silver_storage = silver_storage or load_storage_from_env(DEFAULT_SILVER_STORAGE_PATH, "MINIO_SILVER_BUCKET")
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    enrollment_df = _read_parquet(silver_storage, "silver/enrollment/data.parquet")
    student_df = _read_parquet(silver_storage, "silver/student/data.parquet")
    dropout_df = _read_parquet(silver_storage, "silver/dropout/data.parquet")

    rows_in = len(enrollment_df)

    duck_conn = duckdb.connect(":memory:")
    deduped_df, duplicate_count = dedupe_enrollment(enrollment_df, duck_conn)
    duck_conn.close()

    working_df, quarantine_unknown = check_known_status(deduped_df)
    working_df, quarantine_range = check_semester_within_cohort_range(working_df, student_df)
    working_df, quarantine_consistency = check_dropout_consistency(working_df, dropout_df)
    working_df, quarantine_progression = check_year_level_progression(working_df)

    all_quarantined = pd.concat(
        [quarantine_unknown, quarantine_range, quarantine_consistency, quarantine_progression],
        ignore_index=True,
    )

    _write_parquet(silver_storage, "silver/enrollment/data.parquet", working_df)
    if len(all_quarantined) > 0:
        _write_parquet(silver_storage, "silver_quarantine/enrollment/data.parquet", all_quarantined)

    quarantine_rate = len(all_quarantined) / rows_in if rows_in else 0.0

    # AFTER
    # Task 47: escalate to a categorized, traceable failure -- rather
    # than a SUCCESS row -- once quarantine consumes too much of the
    # batch to trust the output silently.
    if rows_in and quarantine_rate > MAX_QUARANTINE_RATE:
        error = DataQualityFailureError(
            f"Silver enrollment validation quarantined {len(all_quarantined)}/{rows_in} rows "
            f"({quarantine_rate:.1%}) -- exceeds the {MAX_QUARANTINE_RATE:.0%} tolerance.",
            stage="Silver Validation & Dedup", entity="enrollment", rows_affected=len(all_quarantined),
            details={
                "quarantined_unknown_status": len(quarantine_unknown),
                "quarantined_range": len(quarantine_range),
                "quarantined_consistency": len(quarantine_consistency),
                "quarantined_progression": len(quarantine_progression),
            },
        )
        record_run(
            meta_conn, run_id, batch_id=run_id, stage=STAGE, entity="enrollment", partition_key="all",
            started_at=started_at, status="FAILED", rows_in=rows_in, source_path="silver/enrollment",
            error_message=error.message, error_category=error.category.value, rows_affected=error.rows_affected,
        )
        if owns_conn:
            meta_conn.close()
        raise error

    # Task 47: a very high raw duplicate rate (as opposed to
    # cross-entity/status quarantine) gets its own DUPLICATE_DATA
    # category -- distinct diagnosis from "the data is malformed".
    duplicate_rate = duplicate_count / rows_in if rows_in else 0.0
    if rows_in and duplicate_rate > MAX_QUARANTINE_RATE:
        error = DuplicateDataError(
            f"Silver enrollment dedup dropped {duplicate_count}/{rows_in} rows ({duplicate_rate:.1%}) "
            f"as duplicates -- exceeds the {MAX_QUARANTINE_RATE:.0%} tolerance; check upstream ingestion "
            f"for a re-sent batch.",
            stage="Silver Validation & Dedup", entity="enrollment", rows_affected=duplicate_count,
        )
        record_run(
            meta_conn, run_id, batch_id=run_id, stage=STAGE, entity="enrollment", partition_key="all",
            started_at=started_at, status="FAILED", rows_in=rows_in, source_path="silver/enrollment",
            error_message=error.message, error_category=error.category.value, rows_affected=error.rows_affected,
        )
        if owns_conn:
            meta_conn.close()
        raise error

    record_run(
        meta_conn, run_id, batch_id=run_id, stage=STAGE, entity="enrollment", partition_key="all",
        started_at=started_at, status="SUCCESS", rows_in=rows_in, rows_out=len(working_df),
        source_path="silver/enrollment",
        error_message=f"quarantined={len(all_quarantined)} ({quarantine_rate:.2%}), duplicates_dropped={duplicate_count}",
    )

    if owns_conn:
        meta_conn.close()

    return {
        "rows_in": rows_in,
        "duplicates_dropped": duplicate_count,
        "quarantined_unknown_status": len(quarantine_unknown),
        "quarantined_range": len(quarantine_range),
        "quarantined_consistency": len(quarantine_consistency),
        "quarantined_progression": len(quarantine_progression),
        "total_quarantined": len(all_quarantined),
        "quarantine_rate": quarantine_rate,
        "rows_out": len(working_df),
    }


if __name__ == "__main__":
    import uuid as _uuid
    from pipelines.common.logging_config import PipelineStageLogger, get_logger

    _logger = get_logger(__name__)
    _run_id = str(_uuid.uuid4())
    with PipelineStageLogger(_run_id, stage="validation", entity="enrollment") as stage_log:
        summary = process_enrollment()
        stage_log.rows_processed = summary["rows_out"]
        stage_log.rows_rejected = summary["total_quarantined"]
        _logger.info(
            "Silver validation + dedup complete (enrollment): %s", summary,
            extra={"pipeline_extra": summary},
        )