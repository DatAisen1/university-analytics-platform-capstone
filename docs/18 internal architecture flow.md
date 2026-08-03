# 18 — Internal Architecture Flow

> **Scope note:** this document covers the internal Data Engineering / Data
> Science pipeline only — the linear path from raw source data to a deployed
> forecast. It deliberately does **not** document the Web/Supabase
> integration, since that boundary is not implemented yet. See
> `docs/15_Tooling_Responsibility_Matrix.md` for the ownership split and
> `docs/17_Consumption_Boundary_MinIO_Supabase.md` for the (separately
> tracked) future consumption boundary.

## 1. The Pipeline, End to End

```
Data Source
  ↓
Ingestion
  ↓
Bronze
  ↓
Silver
  ↓
Gold
  ↓
Warehouse
  ↓
Feature Engineering
  ↓
ML
  ↓
Forecast
```

This is a strictly linear dependency chain — each stage consumes only the
output of the stage directly above it. It is implemented as ten Dagster
`@asset`s in `orchestration/assets.py`, wired together with explicit
`deps=[...]` edges that mirror the diagram exactly:

```python
all_assets = [
    ingestion,
    bronze,
    silver,
    validation,
    gold,
    warehouse,
    features,
    training,
    evaluation,
    forecast,
]
```

Every asset is wrapped by the same `_track_asset_run()` helper, which
records a `RUNNING` → `SUCCESS`/`FAILED` row per stage (via
`pipelines.common.metadata.record_pipeline_run`) and, on failure, converts
whatever exception was raised into a categorized `PipelineError`
(`pipelines/common/errors.py`) before re-raising it. This gives every stage
the same audit trail and the same structured failure report, regardless of
which stage fails.

## 2. Stage-by-Stage Responsibility

### Data Source
Semester extract files (per-entity CSV/Parquet) representing the raw
university records for a given academic year/semester. Not owned by this
repository — it is the inbound handoff point.

### Ingestion
`pipelines/ingestion/ingest_to_bronze.py` (`ingest_all()`). Reads the raw
extract, performs the minimal file-level checks (file exists, required
columns present), tags each row with ingestion metadata, and hands off to
Bronze storage. `pipelines/ingestion/audit_bronze.py` provides a
post-ingestion audit pass.

### Bronze
Raw, untouched-in-substance data, stored as Parquet under `bronze/<entity>/`
via `pipelines.common.storage.ObjectStorage` (local filesystem or MinIO,
selected by `pipelines/common/config.py`). Bronze is validated for **shape
only** — `pipelines/common/schemas.py` — not for business correctness. Its
own module docstring is explicit about this: an `enrollment_status` column
is deliberately **not** restricted to a controlled vocabulary at this layer,
because Bronze intentionally preserves the messy, realistic text variants a
real source system would send (`' ENROLLED '`, `'DROPPED OUT'`, etc.) — that
is Silver's job to normalize, not Bronze's job to reject.

### Silver
Three sub-stages, run in sequence:
1. **Cleaning** — `pipelines/silver/clean_entities.py` and
   `pipelines/silver/cleaning_rules.py` coerce Bronze's raw shapes into
   canonical dtypes and controlled vocabularies.
2. **Validate & dedupe** — `pipelines/silver/validate_and_dedupe.py`
   (per-entity schema validation against
   `pipelines/common/silver_schemas.py`) and
   `pipelines/silver/progression_validation.py` (year-level/progression
   consistency).
3. **Business rules** — `pipelines/silver/business_rules.py`, the
   `validation` Dagster asset. This is where **cross-entity, relational**
   checks live (a program's `college_id` must exist; a fact row's
   `(program_id, college_id)` pair must agree with the program dimension;
   semester/academic-year/year-level plausibility; non-negative counts).
   Every check quarantines bad rows to `silver_quarantine/<entity>/...`
   rather than silently dropping them, and a quarantine rate above 25% on
   any single check (`MAX_QUARANTINE_RATE`) escalates to a hard pipeline
   failure rather than a quiet `SUCCESS`.

### Gold
`pipelines/gold/build_dimensions.py`, `build_facts.py`, and `build_kpi.py`
(the `gold` Dagster asset) build the star schema: dimension tables
(`dim_college`, `dim_program`, `dim_student`, `dim_academic_period`, …),
fact tables (`fact_enrollment`, `fact_graduation`, `fact_dropout`,
`fact_shifter`), and the college-grain `fact_institution_kpi` table. This is
the layer where the canonical dataset contract
(`pipelines/common/canonical_schema.py`) is enforced.

### Warehouse
`pipelines/gold/load_gold_to_postgres.py` (the `warehouse` asset) loads the
Gold layer's DuckDB-computed tables into PostgreSQL, which is the system of
record every downstream stage (features, ML, forecast) reads from. DDL for
the warehouse lives under `warehouse/ddl/*.sql`, applied in order by
`pipelines/common/migrations.py`.

### Feature Engineering
`pipelines/gold/build_ml_features.py` (the `features` asset) builds two
leakage-safe feature tables — `gold.ml_program_forecast_features` and
`gold.ml_enrollment_features_by_year_level` — directly from
`gold.fact_enrollment` / `gold.fact_graduation` in the warehouse, using SQL
window functions ordered by `period_ordinal`. See §3 of
`19_Data_Contracts.md` and `20_ML_Assumptions.md` for the leakage-prevention
constraint in detail.

### ML
`models/forecasting/train_prophet.py` (the `training` and `evaluation`
assets) trains one Prophet model per `(college, metric)` series, walk-forward
evaluates it against two baselines, and writes `forecasting/artifacts/`
(pickled models + `evaluation_report.csv`/`.md`).

### Forecast
`models/forecasting/deploy_forecast.py` and
`models/forecasting/model_registry.py` (the `forecast` asset) run the
retrain-gate → candidate → evaluate → compare → promote workflow per series,
and write only **promoted** models' predictions to `gold.fact_forecast`.

## 3. Orchestration Mechanics

- **Engine:** Dagster (`orchestration/definitions.py` wires `all_assets`
  into a `Definitions` object).
- **Execution unit:** one `@asset` per pipeline stage, each a thin wrapper
  around the corresponding `pipelines.*` / `models.*` module's public entry
  point — the assets module contains no business logic itself.
- **Dependency declaration:** explicit `deps=[<upstream_asset>]` per asset,
  which is what makes the diagram in §1 an enforced dependency graph rather
  than just documentation.
- **Run tracking:** every asset records `records_processed` and
  stage-specific metadata (row counts, quarantine rate, saved model paths,
  deployment decisions) via `context.add_output_metadata`, visible in the
  Dagster UI per run.
- **Failure handling:** any exception raised inside a stage is classified
  into one of the 14 `PipelineErrorCategory` values
  (`pipelines/common/errors.py`) — schema, academic-year, semester,
  year-level, duplicate-data, data-quality, storage-backend (MinIO/DuckDB/
  Postgres/dbt), feature-engineering, model-training, model-evaluation, or
  forecast errors — recorded to the pipeline-run metadata table, logged as a
  structured "Stage / Error / Rows affected" report, and re-raised so
  Dagster marks the run failed. No stage silently swallows an error to keep
  the DAG green.

## 4. What Is Explicitly Out of Scope Here

Per this task's instructions, this document does **not** cover the
Web/Supabase consumption layer. That boundary (a read-only
`web_service_reader` Postgres role scoped to `gold`/`marts`) is described,
as a *planned* interface, in `docs/06_Data_Warehouse.md` §5 and
`docs/17_Consumption_Boundary_MinIO_Supabase.md`, but no code in this
pipeline currently implements a Supabase or web-facing integration — the
pipeline's own last stage is `forecast`, writing to
`gold.fact_forecast`/`gold.model_registry` in PostgreSQL.    