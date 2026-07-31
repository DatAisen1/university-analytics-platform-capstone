"""
pipelines/silver/clean_entities.py

Bronze -> Silver cleaning stage: reads ALL Bronze partitions for an
entity (across every academic_year/semester AND every ingestion batch --
a global read, not per-partition, because Silver needs the full picture
to eventually dedupe across partitions in Day 11's late-correction
handling), applies text-hygiene and enrollment_status normalization via
DuckDB SQL (per docs/07_Technology_Stack.md's "DuckDB SQL for the actual
Bronze->Silver->Gold transformations" decision), and writes one Silver
Parquet file per entity.

What this stage explicitly does NOT do (see docs/05_Medallion_Architecture.md):
  - No deduplication of duplicate/late-correction rows (Day 11).
  - No business-rule quarantine (Day 11).
  - No rejection of unmappable enrollment_status values -- they're tagged
    'UNKNOWN:<raw>' and left in the output for Day 11 to quarantine.

Run via: python -m pipelines.silver.clean_entities
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import pandas as pd

from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage, ObjectStorage
from pipelines.silver.cleaning_rules import normalize_enrollment_status_safe

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRONZE_STORAGE_PATH = _REPO_ROOT / "warehouse" / "bronze_store"
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"

STAGE = "silver_cleaning"

# Text columns to trim per entity, via SQL TRIM() -- not exhaustive of
# every column, just the ones that are actually free text (IDs and
# already-controlled fields don't need it, but trimming them is harmless
# and defensive against future noise sources).
TEXT_COLUMNS: Dict[str, List[str]] = {
    "college": ["college_id", "college_name"],
    "program": ["program_id", "program_name", "college_id", "program_level"],
    "student": ["student_id", "gender", "home_province", "admission_type",
                "entry_college_id", "entry_program_id"],
    "enrollment": ["student_id", "college_id", "program_id"],  # enrollment_status handled separately
    "graduation": ["student_id", "program_id", "college_id"],
    "dropout": ["student_id", "program_id", "college_id", "dropout_reason"],
    "shifter": ["student_id", "from_program_id", "to_program_id"],
}

ALL_ENTITIES = list(TEXT_COLUMNS.keys())


def read_all_bronze(storage: ObjectStorage, entity: str) -> pd.DataFrame:
    """Union every Bronze Parquet file for `entity`, across all partitions
    and ingestion batches. This IS the global read Silver needs -- Bronze
    stores one file per (entity, partition, batch); Silver logically
    treats an entity as one table."""
    keys = storage.list_keys(f"bronze/{entity}")
    if not keys:
        raise FileNotFoundError(f"No Bronze data found for entity {entity!r} -- run Day 8's ingestion first")
    frames = [pd.read_parquet(io.BytesIO(storage.read_bytes(k))) for k in keys]
    return pd.concat(frames, ignore_index=True)


def _build_trim_select(df_columns: List[str], text_columns: List[str]) -> str:
    """Build a SELECT clause that TRIM()s the given text columns and
    passes every other column through unchanged."""
    parts = []
    for col in df_columns:
        if col in text_columns:
            parts.append(f'TRIM("{col}") AS "{col}"')
        else:
            parts.append(f'"{col}"')
    return ", ".join(parts)


def clean_entity(df: pd.DataFrame, entity: str, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Apply DuckDB SQL cleaning to one entity's Bronze data. Registers
    the incoming DataFrame as a DuckDB view, builds a SELECT with TRIM()
    on the entity's text columns (and, for enrollment, the
    normalize_enrollment_status_safe UDF on enrollment_status), and
    returns the cleaned result as a DataFrame.
    """
    conn.register("bronze_view", df)

    text_columns = TEXT_COLUMNS[entity]

    if entity == "enrollment":
        conn.create_function(
            "normalize_status", normalize_enrollment_status_safe, ["VARCHAR"], "VARCHAR"
        )
        parts = []
        for col in df.columns:
            if col == "enrollment_status":
                parts.append('normalize_status("enrollment_status") AS "enrollment_status"')
            elif col in text_columns:
                parts.append(f'TRIM("{col}") AS "{col}"')
            else:
                parts.append(f'"{col}"')
        select_clause = ", ".join(parts)
    else:
        select_clause = _build_trim_select(list(df.columns), text_columns)

    result = conn.execute(f"SELECT {select_clause} FROM bronze_view").df()
    conn.unregister("bronze_view")
    if entity == "enrollment":
        conn.remove_function("normalize_status")
    return result


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def clean_all(
    bronze_storage: Optional[ObjectStorage] = None,
    silver_storage: Optional[ObjectStorage] = None,
    meta_conn=None,
    entities: Optional[List[str]] = None,
) -> List[Dict[str, object]]:
    """Clean every entity's full Bronze dataset and write one Silver
    Parquet file per entity. Returns per-entity summaries."""
    bronze_storage = bronze_storage or LocalFileStorage(DEFAULT_BRONZE_STORAGE_PATH)
    silver_storage = silver_storage or LocalFileStorage(DEFAULT_SILVER_STORAGE_PATH)
    owns_conn = meta_conn is None
    meta_conn = meta_conn or get_connection()
    entities = entities or ALL_ENTITIES

    duck_conn = duckdb.connect(":memory:")
    results: List[Dict[str, object]] = []

    for entity in entities:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        try:
            raw_df = read_all_bronze(bronze_storage, entity)
            cleaned_df = clean_entity(raw_df, entity, duck_conn)
            key = f"silver/{entity}/data.parquet"
            _write_parquet(silver_storage, key, cleaned_df)

            unknown_count = 0
            if entity == "enrollment":
                unknown_count = int(cleaned_df["enrollment_status"].astype(str).str.startswith("UNKNOWN:").sum())

            record_run(
                meta_conn, run_id, batch_id=run_id, stage=STAGE, entity=entity, partition_key="all",
                started_at=started_at, status="SUCCESS", rows_in=len(raw_df), rows_out=len(cleaned_df),
                source_path=f"bronze/{entity}",
            )
            results.append({
                "entity": entity, "status": "SUCCESS", "rows": len(cleaned_df),
                "unknown_status_count": unknown_count, "key": key,
            })
        except FileNotFoundError as exc:
            record_run(
                meta_conn, run_id, batch_id=run_id, stage=STAGE, entity=entity, partition_key="all",
                started_at=started_at, status="FAILED", error_message=str(exc),
            )
            results.append({"entity": entity, "status": "FAILED", "error": str(exc)})

    duck_conn.close()
    if owns_conn:
        meta_conn.close()
    return results


if __name__ == "__main__":
    summary = clean_all()
    print("Silver cleaning complete:")
    for r in summary:
        if r["status"] == "SUCCESS":
            extra = f", unknown_status={r['unknown_status_count']}" if r.get("unknown_status_count") else ""
            print(f"  {r['entity']}: {r['rows']} rows{extra}")
        else:
            print(f"  {r['entity']}: FAILED -- {r['error']}")
