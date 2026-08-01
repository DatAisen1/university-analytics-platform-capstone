"""
pipelines/silver/clean_entities.py

Bronze -> Silver cleaning stage: reads ALL Bronze partitions for an
entity (across every academic_year/semester AND every ingestion batch --
a global read, not per-partition, because Silver needs the full picture
to eventually dedupe across partitions in the validate_and_dedupe stage's
late-correction handling), and applies every Silver TRANSFORMATION step
via DuckDB SQL + pandas (per docs/07_Technology_Stack.md's "DuckDB SQL
for the actual Bronze->Silver->Gold transformations" decision), writing
one Silver Parquet file per entity.

Each entity goes through six explicit, named stages, in order, inside
`clean_entity`:

    1. TEXT HYGIENE        -- TRIM() on free-text columns.
    2. NULL HANDLING        -- an empty string after trimming becomes a
                                real null (NULLIF), not a different-looking
                                kind of "nothing".
    3. CATEGORICAL STANDARDIZATION -- controlled-vocabulary columns
                                (enrollment_status, gender, admission_type,
                                program_level) are case-folded onto their
                                canonical spelling; anything unrecognized
                                is tagged 'UNKNOWN:<raw>', never dropped.
    4. ACADEMIC-YEAR / SEMESTER NORMALIZATION -- academic_year-bearing and
                                semester_number-bearing columns are coerced
                                to Gold's canonical plain-int representation
                                regardless of the raw value's format.
    5. TYPE CONVERSION      -- every column is cast to its canonical
                                Silver dtype (nullable Int64/string/
                                boolean/float64), so downstream Silver/Gold
                                code can rely on dtype, not guess at it.
    6. DUPLICATE HANDLING    -- exact duplicate rows (identical on every
                                BUSINESS column, ignoring Bronze's audit
                                columns) are collapsed to one, keeping the
                                most-recently-ingested copy.

What this stage explicitly does NOT do (see docs/05_Medallion_Architecture.md):
  - No natural-key/late-correction dedup (that's validate_and_dedupe.py's
    job for enrollment -- a different, semantic kind of dedup from this
    stage's literal exact-duplicate collapse).
  - No cross-entity/business-rule quarantine (pipelines/silver/
    business_rules.py).
  - No rejection of unmappable categorical values -- they're tagged
    'UNKNOWN:<raw>' and left in the output for business_rules.py /
    validate_and_dedupe.py to quarantine.

Run via: python -m pipelines.silver.clean_entities
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd

from pipelines.common.academic_periods import academic_year_start_year
from pipelines.common.metadata import get_connection, record_run
from pipelines.common.storage import LocalFileStorage, ObjectStorage
from pipelines.silver.cleaning_rules import (
    make_categorical_normalizer,
    normalize_enrollment_status_safe,
    normalize_semester_number_safe,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRONZE_STORAGE_PATH = _REPO_ROOT / "warehouse" / "bronze_store"
DEFAULT_SILVER_STORAGE_PATH = _REPO_ROOT / "warehouse" / "silver_store"

STAGE = "silver_cleaning"
SCHEMA_VALIDATION_STAGE = "silver_schema_validation"

# Bronze audit columns stamped at ingestion -- excluded from the
# exact-duplicate business-column comparison (Stage 6) since two
# ingestion batches can legitimately carry byte-identical business data
# with only these differing.
AUDIT_COLUMNS = ["_ingested_at", "_source_file", "_batch_id"]

# Stage 1/2: text columns to TRIM + null-normalize per entity -- not
# exhaustive of every column, just the ones that are actually free text
# (IDs and already-controlled fields don't need it, but trimming them is
# harmless and defensive against future noise sources). Columns handled
# by Stage 3's categorical normalizer are NOT listed here -- they get
# their own UDF branch instead of a plain TRIM.
TEXT_COLUMNS: Dict[str, List[str]] = {
    "college": ["college_id", "college_name"],
    "program": ["program_id", "program_name", "college_id"],
    "student": ["student_id", "home_province", "entry_college_id", "entry_program_id"],
    "enrollment": ["student_id", "college_id", "program_id"],  # enrollment_status handled separately
    "graduation": ["student_id", "program_id", "college_id"],
    "dropout": ["student_id", "program_id", "college_id", "dropout_reason"],
    "shifter": ["student_id", "from_program_id", "to_program_id"],
}

# Stage 3: small controlled-vocabulary columns per entity, normalized via
# make_categorical_normalizer. enrollment_status is deliberately NOT
# listed here -- it already has its own dedicated, previously-shipped
# normalize_enrollment_status_safe function and DuckDB UDF wiring below.
CATEGORICAL_COLUMNS: Dict[str, Dict[str, frozenset]] = {
    "student": {
        "gender": frozenset({"Male", "Female"}),
        "admission_type": frozenset({"Freshman", "Transferee"}),
    },
    "program": {
        "program_level": frozenset({"Bachelor", "Certificate", "Diploma"}),
    },
}

# Stage 4: which column(s) on each entity carry an academic-year value
# vs. a semester_number value, so the right normalizer is applied to the
# right column without a bespoke branch per entity.
ACADEMIC_YEAR_COLUMNS: Dict[str, List[str]] = {
    "student": ["cohort_academic_year"],
    "enrollment": ["academic_year"],
    "graduation": ["academic_year"],
    "dropout": ["academic_year"],
    "shifter": ["academic_year"],
}
SEMESTER_NUMBER_COLUMNS: Dict[str, str] = {
    "enrollment": "semester_number",
    "graduation": "semester_number",
    "dropout": "semester_number",
    "shifter": "semester_number",
}

# Stage 5: canonical Silver dtype per column, per entity. Nullable pandas
# extension dtypes (Int64/string/boolean) are used deliberately: a value
# that fails to convert becomes a tracked null instead of raising and
# aborting the whole batch, consistent with this project's "tag/report,
# don't crash a whole batch over one bad row" philosophy (see
# normalize_enrollment_status_safe). Plain "float64" is used for genuine
# floating-point metrics (numpy has no nullable-float ambiguity here).
TARGET_DTYPES: Dict[str, Dict[str, str]] = {
    "college": {"college_id": "string", "college_name": "string"},
    "program": {
        "program_id": "string", "program_name": "string", "college_id": "string",
        "program_level": "string", "nominal_duration_years": "float64",
    },
    "student": {
        "student_id": "string", "cohort_academic_year": "Int64", "gender": "string",
        "birth_year": "Int64", "home_province": "string", "admission_type": "string",
        "entry_year_level": "Int64", "entry_college_id": "string", "entry_program_id": "string",
    },
    "enrollment": {
        "student_id": "string", "academic_year": "Int64", "semester_number": "Int64",
        "college_id": "string", "program_id": "string", "enrollment_status": "string",
        "year_level": "Int64", "units_enrolled": "Int64", "is_new_enrollee": "boolean",
    },
    "graduation": {
        "student_id": "string", "academic_year": "Int64", "semester_number": "Int64",
        "program_id": "string", "college_id": "string", "years_to_complete": "float64",
    },
    "dropout": {
        "student_id": "string", "academic_year": "Int64", "semester_number": "Int64",
        "program_id": "string", "college_id": "string", "dropout_reason": "string",
        "semesters_completed_before_dropout": "Int64",
    },
    "shifter": {
        "student_id": "string", "academic_year": "Int64", "semester_number": "Int64",
        "from_program_id": "string", "to_program_id": "string",
    },
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
def _normalize_academic_year_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Stage 4a (ACADEMIC-YEAR NORMALIZATION): coerce every academic-year-
    bearing column to the canonical plain-int representation Gold and
    this project's own Silver business rules already key on (e.g. 2021),
    regardless of whether the raw value arrived as an int, a numeric
    string, or a '2021-2022' label -- academic_year_start_year() already
    handles all three. Silver deliberately keeps the INT form rather than
    a 'YYYY-YYYY' label: converting to the label here would break every
    downstream Gold join, which is written against the int
    (pipelines/gold/build_dimensions.py's ACADEMIC_YEARS and
    semester_ordinal()). Nulls are left as nulls, not defaulted to
    OBSERVED_START_YEAR -- academic_year_start_year(None) would silently
    manufacture 2021 for a genuinely missing value, which is exactly the
    kind of silent decision Stage 2's null handling exists to make
    visible, not hide again here.
    """
    df = df.copy()
    for col in columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: academic_year_start_year(v) if pd.notna(v) else v)
    return df


def _coerce_dtypes(df: pd.DataFrame, entity: str) -> Tuple[pd.DataFrame, int]:
    """Stage 5 (TYPE CONVERSION): cast every column to its canonical
    Silver dtype from TARGET_DTYPES. Uses pandas' nullable dtypes so a
    value that can't convert becomes a tracked null instead of raising
    and aborting the whole batch. Returns (coerced_df, failure_count)
    where failure_count is how many NEW nulls this step introduced --
    i.e. values that were present but uncoercible, not values that were
    already null coming in.
    """
    df = df.copy()
    failures = 0
    for col, dtype in TARGET_DTYPES.get(entity, {}).items():
        if col not in df.columns:
            continue
        before_null = int(df[col].isna().sum())
        if dtype == "Int64":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif dtype == "float64":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        elif dtype == "boolean":
            df[col] = df[col].astype("boolean")
        else:  # "string"
            df[col] = df[col].astype("string")
        after_null = int(df[col].isna().sum())
        failures += max(0, after_null - before_null)
    return df, failures


def _drop_exact_duplicates(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Stage 6 (DUPLICATE HANDLING): drop rows that are exact duplicates
    on every BUSINESS column (i.e. ignoring AUDIT_COLUMNS, since two
    ingestion batches can legitimately carry byte-identical business data
    with only _ingested_at/_batch_id differing). Keeps the most-recently-
    ingested copy when duplicates are found. This is STRUCTURAL,
    literal-duplicate collapse -- distinct from validate_and_dedupe.py's
    semantic, natural-key dedup for enrollment (which additionally
    resolves late CORRECTIONS, i.e. two genuinely different rows for the
    same key) -- and applies to every entity, not just enrollment, which
    previously had no duplicate handling at all outside enrollment.
    """
    business_columns = [c for c in df.columns if c not in AUDIT_COLUMNS]
    before = len(df)
    if "_ingested_at" in df.columns:
        df = df.sort_values("_ingested_at").drop_duplicates(subset=business_columns, keep="last")
    else:
        df = df.drop_duplicates(subset=business_columns, keep="last")
    df = df.reset_index(drop=True)
    return df, before - len(df)


def clean_entity(
    df: pd.DataFrame, entity: str, conn: duckdb.DuckDBPyConnection
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """Run every Task-20 transformation stage for one entity's full
    Bronze dataset, in order (see module docstring for the full list),
    and return (cleaned_df, stats) where stats reports counts useful for
    observability -- never used to silently hide a problem, only to
    surface one for the caller to log/act on.
    """
    text_columns = TEXT_COLUMNS.get(entity, [])
    categorical_columns = CATEGORICAL_COLUMNS.get(entity, {})

    # --- Stages 1-3: SQL text hygiene + null handling + categorical
    # standardization, all in one SELECT pass. ---
    conn.register("bronze_view", df)
    registered_udfs: List[str] = []
    parts: List[str] = []
    for col in df.columns:
        if entity == "enrollment" and col == "enrollment_status":
            conn.create_function("normalize_status", normalize_enrollment_status_safe, ["VARCHAR"], "VARCHAR")
            registered_udfs.append("normalize_status")
            parts.append('normalize_status("enrollment_status") AS "enrollment_status"')
        elif col in categorical_columns:
            udf_name = f"normalize_{col}"
            conn.create_function(
                udf_name, make_categorical_normalizer(categorical_columns[col]), ["VARCHAR"], "VARCHAR"
            )
            registered_udfs.append(udf_name)
            parts.append(f'{udf_name}("{col}") AS "{col}"')
        elif col in text_columns:
            # TRIM (Stage 1) + NULLIF (Stage 2) in one expression: an
            # empty string after trimming means "no value", not "empty text".
            parts.append(f'NULLIF(TRIM("{col}"), \'\') AS "{col}"')
        else:
            parts.append(f'"{col}"')
    select_clause = ", ".join(parts)

    cleaned = conn.execute(f"SELECT {select_clause} FROM bronze_view").df()
    conn.unregister("bronze_view")
    for udf_name in registered_udfs:
        conn.remove_function(udf_name)

    unknown_status_count = 0
    if entity == "enrollment":
        unknown_status_count = int(cleaned["enrollment_status"].astype(str).str.startswith("UNKNOWN:").sum())
    unknown_category_count = sum(
        int(cleaned[col].astype(str).str.startswith("UNKNOWN:").sum())
        for col in categorical_columns if col in cleaned.columns
    )

    # --- Stage 4: academic-year / semester normalization ---
    cleaned = _normalize_academic_year_columns(cleaned, ACADEMIC_YEAR_COLUMNS.get(entity, []))
    semester_col = SEMESTER_NUMBER_COLUMNS.get(entity)
    if semester_col and semester_col in cleaned.columns:
        cleaned[semester_col] = cleaned[semester_col].apply(normalize_semester_number_safe)

    # --- Stage 5: type conversion ---
    cleaned, coercion_failures = _coerce_dtypes(cleaned, entity)

    # --- Stage 6: duplicate handling ---
    cleaned, duplicates_dropped = _drop_exact_duplicates(cleaned)

    stats = {
        "unknown_status_count": unknown_status_count,
        "unknown_category_count": unknown_category_count,
        "type_coercion_failures": coercion_failures,
        "duplicates_dropped": duplicates_dropped,
    }
    return cleaned, stats


def _write_parquet(storage: ObjectStorage, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    storage.write_bytes(key, buffer.getvalue())


def _run_silver_schema_validation(
    meta_conn, entity: str, df: pd.DataFrame, source_path: str
) -> Dict[str, object]:
    """Task 21: validate `df` against pipelines/common/silver_schemas.py
    and log the result under SCHEMA_VALIDATION_STAGE. Non-blocking,
    mirroring Bronze's _run_schema_validation (pipelines/ingestion/
    ingest_to_bronze.py) -- a Silver schema failure here means Stage
    1-6's cleaning logic has a bug (it's supposed to already GUARANTEE
    this shape), which is worth surfacing loudly in the run log, but
    aborting Silver entirely on it would take the whole pipeline down for
    what is, in every real run, a cleaning-code regression to fix, not a
    per-batch data problem to quarantine mid-run.
    """
    import pandera.errors

    from pipelines.common.silver_schemas import validate_silver_dataframe

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    try:
        validate_silver_dataframe(df, entity)
        record_run(
            meta_conn, run_id, batch_id=run_id, stage=SCHEMA_VALIDATION_STAGE, entity=entity,
            partition_key="all", started_at=started_at, status="SUCCESS",
            rows_in=len(df), rows_out=len(df), source_path=source_path,
        )
        return {"status": "SCHEMA_VALID"}
    except pandera.errors.SchemaErrors as exc:
        failure_count = len(exc.failure_cases)
        summary = (
            f"{failure_count} schema violation(s): "
            + "; ".join(
                f"{row['column']}: {row['check']}"
                for _, row in exc.failure_cases.head(5).iterrows()
            )
        )
        record_run(
            meta_conn, run_id, batch_id=run_id, stage=SCHEMA_VALIDATION_STAGE, entity=entity,
            partition_key="all", started_at=started_at, status="FAILED",
            rows_in=len(df), source_path=source_path, error_message=summary,
        )
        return {"status": "SCHEMA_INVALID", "violation_count": failure_count, "summary": summary}


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
            cleaned_df, stats = clean_entity(raw_df, entity, duck_conn)
            key = f"silver/{entity}/data.parquet"
            _write_parquet(silver_storage, key, cleaned_df)

            notable = {k: v for k, v in stats.items() if v}
            record_run(
                meta_conn, run_id, batch_id=run_id, stage=STAGE, entity=entity, partition_key="all",
                started_at=started_at, status="SUCCESS", rows_in=len(raw_df), rows_out=len(cleaned_df),
                source_path=f"bronze/{entity}",
                error_message=", ".join(f"{k}={v}" for k, v in notable.items()) if notable else "",
            )
            schema_result = _run_silver_schema_validation(meta_conn, entity, cleaned_df, key)
            results.append({
                "entity": entity, "status": "SUCCESS", "rows": len(cleaned_df), "key": key,
                "schema_validation": schema_result["status"],
                **stats,
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