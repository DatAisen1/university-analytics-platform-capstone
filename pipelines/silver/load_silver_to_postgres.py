"""
pipelines/silver/load_silver_to_postgres.py

Materializes Silver's Parquet output (warehouse/silver_store/) into real
tables in Postgres's `silver` schema, exactly the way
pipelines/gold/load_gold_to_postgres.py already does for Gold.

Task 25 fix: `silver` was an empty schema with grants pointing at it
(warehouse/ddl/002_grants.sql) but no table ever created there, which is
why a Silver-layer uniqueness constraint (e.g.
uq_silver_program_program_id) couldn't exist -- there was no table to
attach it to, and nothing loaded data into one. warehouse/ddl/
004_silver_star_schema.sql now defines those tables with their full
constraints; this loader is what actually gets Silver's Parquet output
into them.

Idempotency (Task 26/27): uses the same TRUNCATE-safe
pipelines.common.postgres.replace_table_contents() Gold already uses --
running this twice, or after a prior partial failure, converges to the
same end state: whatever the CURRENT Silver Parquet output is, exactly
once per row, never doubled. It also requires migrations to have already
run (pipelines.common.postgres.MissingTableError otherwise) rather than
falling back to creating an unconstrained table.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import create_engine

from pipelines.common.postgres import replace_table_contents
from pipelines.common.storage import LocalFileStorage, ObjectStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"

SILVER_TABLES = ["college", "program", "student", "enrollment", "graduation", "dropout", "shifter"]


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def build_pipeline_writer_engine(password: str, env: Optional[dict] = None):
    """Build a SQLAlchemy engine connected as pipeline_writer. `env` is
    forwarded to pipelines.common.settings.get_postgres_settings -- pass
    an explicit mapping (as tests do) to bypass the real process
    environment."""
    from pipelines.common.settings import get_postgres_settings

    settings = get_postgres_settings(env)
    return create_engine(
        f"postgresql+psycopg2://pipeline_writer:{password}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


def load_silver_to_postgres(
    engine,
    silver_storage: Optional[ObjectStorage] = None,
    tables: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Load each Silver Parquet table into Postgres's silver schema, via
    the shared TRUNCATE-safe writer. Raises
    pipelines.common.postgres.MissingTableError if migrations haven't
    been applied yet -- run pipelines.common.migrations.apply_migrations()
    (or the warehouse bootstrap) first.
    """
    from pipelines.common.migrations import assert_up_to_date

    assert_up_to_date(engine.raw_connection())  # Task 25: fail loudly, not via a silent to_sql table

    silver_storage = silver_storage or LocalFileStorage(DEFAULT_SILVER_STORAGE_PATH)
    tables = tables or SILVER_TABLES

    row_counts: Dict[str, int] = {}
    for table_name in tables:
        df = _read_parquet(silver_storage, f"silver/{table_name}/data.parquet")
        # Postgres columns are NOT NULL per 004_silver_star_schema.sql;
        # drop the Bronze/Silver-internal audit/quarantine columns that
        # aren't part of the governed Silver schema for these tables.
        df = df[[c for c in df.columns if not c.startswith("_")]]
        replace_table_contents(engine, "silver", table_name, df)
        row_counts[table_name] = len(df)

    return row_counts


if __name__ == "__main__":
    from pipelines.common.settings import get_postgres_settings

    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)
    counts = load_silver_to_postgres(engine)
    print("Silver -> Postgres load complete:")
    for name, count in counts.items():
        print(f"  silver.{name}: {count} rows")