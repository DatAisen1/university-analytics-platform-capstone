"""
pipelines/gold/load_gold_to_postgres.py

Materializes the Gold layer's Parquet output (warehouse/gold_store/,
built in Week 2's build_dimensions.py / build_facts.py / build_kpi.py)
into real tables in Postgres's `gold` schema, so dbt (Day 16+) has
genuine tables to build staging models on top of.

Connects as pipeline_writer specifically (not admin) -- this is the same
role the real pipeline is documented to use for Bronze/Silver/Gold writes
(docs/06_Data_Warehouse.md Section 5), and it's *why* Day 15's
`ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_writer IN SCHEMA gold ...`
clause exists: tables created here automatically become readable by
dbt_role and dashboard_reader with no extra grant statement needed.

This step didn't exist as its own roadmap day -- it's a necessary bridge
now that Day 15 made a real Postgres warehouse available: Week 2's Gold
build produced correct, tested Parquet output; this is what actually
gets it into the warehouse those tables were designed for all along.
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
DEFAULT_GOLD_STORAGE_PATH = _REPO_ROOT / "warehouse" / "gold_store"

GOLD_TABLES = [
    "dim_academic_year", "dim_semester", "dim_calendar", "dim_college", "dim_program", "dim_student",
    "fact_enrollment", "fact_graduation", "fact_dropout", "fact_shifter", "fact_retention",
    "fact_institution_kpi",
]


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def build_pipeline_writer_engine(password: str, env: Optional[dict] = None):
    import os
    env = env if env is not None else os.environ
    host = env.get("POSTGRES_HOST", "localhost")
    port = env.get("POSTGRES_PORT", "5432")
    db = env.get("POSTGRES_DB", "university_analytics")
    return create_engine(f"postgresql+psycopg2://pipeline_writer:{password}@{host}:{port}/{db}")


def load_gold_to_postgres(
    engine,
    gold_storage: Optional[ObjectStorage] = None,
    tables: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Load each Gold Parquet table into Postgres's gold schema, via the
    shared TRUNCATE-safe writer (pipelines.common.postgres.replace_table_contents)
    -- see that function's docstring for why this must never be a naive
    DROP-based 'replace'.
    """
    gold_storage = gold_storage or LocalFileStorage(DEFAULT_GOLD_STORAGE_PATH)
    tables = tables or GOLD_TABLES

    row_counts: Dict[str, int] = {}
    for table_name in tables:
        df = _read_parquet(gold_storage, f"gold/{table_name}/data.parquet")
        replace_table_contents(engine, "gold", table_name, df)
        row_counts[table_name] = len(df)

    return row_counts


if __name__ == "__main__":
    import os
    password = os.environ["PIPELINE_WRITER_PASSWORD"]
    engine = build_pipeline_writer_engine(password)
    counts = load_gold_to_postgres(engine)
    print("Gold -> Postgres load complete:")
    for name, count in counts.items():
        print(f"  gold.{name}: {count} rows")
