"""
pipelines/common/metadata.py

The pipeline_run_log metadata store described in docs/03_Data_Engineering.md
Section 8 ("Pipeline Monitoring & Metadata Tracking") and docs/06_Data_Warehouse.md's
`meta` schema. Backed by DuckDB -- a real, queryable, file-based database
that runs in-process (no server needed), which is why it's usable here
even without the Postgres warehouse running. In production this table
would live in Postgres's `meta` schema alongside everything else; the
schema and the idempotency logic built on top of it are identical either
way, so nothing here needs to change when Postgres comes online in
Week 3 -- only the connection target does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_META_DB_PATH = _REPO_ROOT / "warehouse" / "meta.duckdb"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_run_log (
    run_id          VARCHAR,
    batch_id        VARCHAR,
    stage           VARCHAR,
    entity          VARCHAR,
    partition_key   VARCHAR,
    started_at      TIMESTAMP,
    ended_at        TIMESTAMP,
    status          VARCHAR,
    rows_in         INTEGER,
    rows_out        INTEGER,
    source_path     VARCHAR,
    error_message   VARCHAR
)
"""


def get_connection(db_path: Path = DEFAULT_META_DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute(_CREATE_TABLE_SQL)
    return conn


def has_successful_run(
    conn: duckdb.DuckDBPyConnection, stage: str, entity: str, partition_key: str
) -> bool:
    """The core idempotency check: has this exact (stage, entity,
    partition_key) combination already completed successfully? If so,
    ingestion should skip it rather than reprocess -- this is what makes
    re-running the ingestion job safe (Day 8's validation checklist)."""
    result = conn.execute(
        "SELECT COUNT(*) FROM pipeline_run_log "
        "WHERE stage = ? AND entity = ? AND partition_key = ? AND status = 'SUCCESS'",
        [stage, entity, partition_key],
    ).fetchone()
    return result[0] > 0


def record_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    batch_id: str,
    stage: str,
    entity: str,
    partition_key: str,
    started_at: datetime,
    status: str,
    rows_in: int = 0,
    rows_out: int = 0,
    source_path: str = "",
    ended_at: Optional[datetime] = None,
    error_message: str = "",
) -> None:
    conn.execute(
        "INSERT INTO pipeline_run_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id, batch_id, stage, entity, partition_key,
            started_at, ended_at or datetime.now(timezone.utc), status,
            rows_in, rows_out, source_path, error_message,
        ],
    )


def get_run_log(conn: duckdb.DuckDBPyConnection):
    """Returns the full run log as a DataFrame -- 'show me every batch
    that quarantined more than 5% of rows' (docs/03) is exactly the kind
    of query this makes possible once quarantine counts are logged
    (Day 11)."""
    return conn.execute("SELECT * FROM pipeline_run_log ORDER BY started_at").df()
