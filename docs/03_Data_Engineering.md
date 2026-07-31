# 03 — Data Engineering Practices

This document defines the engineering discipline layer: how code is organized, how failures are handled, how pipelines are made trustworthy. These are the practices that separate a "script that works on my machine" from a pipeline someone else could operate.

## 1. Repository Structure

```
university-analytics-platform/
├── configs/                  # YAML config: colleges, programs, business rules, env-specific settings
│   ├── colleges.yaml
│   ├── programs.yaml
│   ├── business_rules.yaml
│   └── environments/
│       ├── dev.yaml
│       └── prod.yaml
├── data_generator/           # Synthetic data generator (named to avoid shadowing the `faker` PyPI lib -- see docs/08)
│   ├── generators/
│   ├── rules/
│   └── output/
├── pipelines/
│   ├── ingestion/            # Source → Bronze
│   ├── bronze/
│   ├── silver/
│   ├── gold/
│   └── common/                # shared utils: io, logging, validation
├── warehouse/
│   ├── ddl/                  # table creation scripts
│   └── migrations/
├── dbt/
│   ├── models/
│   │   ├── staging/
│   │   ├── intermediate/
│   │   └── marts/
│   ├── tests/
│   └── dbt_project.yml
├── analytics/                # ad-hoc SQL, exploratory notebooks output
├── models/                   # ML model code (feature engineering, training)
│   └── forecasting/
├── forecasting/               # forecast job entrypoints, artifacts
├── dashboard/                 # Superset/Streamlit assets
├── notebooks/                  # exploration only — never production logic
├── scripts/                   # one-off / operational scripts (backfill, seed, etc.)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── data_quality/
├── docker/
│   ├── docker-compose.yml
│   └── Dockerfile.pipeline
├── logs/
├── docs/
└── README.md
```

**Why this shape:** it mirrors the medallion architecture directly in folder names (`bronze/`, `silver/`, `gold/`), so anyone opening the repo immediately understands where in the lifecycle any given piece of code operates. `configs/` is separated from `pipelines/` deliberately — business facts (which colleges exist, what counts as a dropout) should never be hardcoded inside transformation logic, because that couples "what the rule is" to "how the rule is applied," making both harder to change independently.

## 2. Naming Conventions

| Object | Convention | Example |
|---|---|---|
| Bronze tables | `bronze_<source>_<entity>` | `bronze_sis_enrollment` |
| Silver tables | `silver_<entity>` | `silver_enrollment` |
| Gold fact tables | `fact_<subject>` | `fact_enrollment` |
| Gold dimension tables | `dim_<entity>` | `dim_student`, `dim_program` |
| dbt staging models | `stg_<source>__<entity>` | `stg_sis__enrollment` |
| dbt marts | `mart_<domain>` | `mart_institution_kpi` |
| Python modules | `snake_case.py` | `bronze_to_silver.py` |
| Config keys | `snake_case` | `dropout_threshold_days` |
| Audit columns | `_ingested_at`, `_source_file`, `_batch_id`, `_is_current`, `_valid_from`, `_valid_to` | — |

Consistent naming is a form of documentation — a new engineer should be able to guess which layer a table belongs to from its name alone, without opening it.

## 3. Coding Standards

- **Python**: PEP 8, type hints on all function signatures, docstrings (Google style) on all public functions.
- **SQL**: lowercase keywords are avoided — use UPPERCASE for SQL keywords, snake_case for identifiers, CTEs preferred over nested subqueries for readability, one statement per file per dbt model.
- **No hardcoded literals** — thresholds, college lists, and business rules come from `configs/`.
- **Every pipeline stage is a pure function where possible**: `def transform(df: pd.DataFrame, config: dict) -> pd.DataFrame`, with I/O (reading/writing files) kept at the edges. This is what makes stages unit-testable without spinning up a database.

## 4. Configuration Management & Environment Variables

- All secrets (DB passwords, MinIO keys) via environment variables, loaded through `.env` (git-ignored) + `python-dotenv`, never committed.
- `configs/environments/{dev,prod}.yaml` holds non-secret environment-specific values (paths, batch sizes, log levels).
- A single `Settings` object (e.g., via `pydantic-settings`) loads and validates all config at startup — if a required variable is missing, the pipeline fails **immediately and loudly** at startup, not halfway through a transformation.

### 4.1 Reference Data as Config — Implementation (Day 3)

Colleges and programs are the clearest case of "this must be config, not code" in the whole project: they're pure business facts (what exists), not logic (what to do), and they change on an administrative timescale (a new program added, a college renamed) that shouldn't require a code deploy.

**Files:**
- `configs/colleges.yaml` — 8 colleges, keyed by a stable `college_id` natural key.
- `configs/programs.yaml` — 37 programs, each pointing at its owning college via `college_id`, carrying `program_level` and `nominal_duration_years` (the latter feeds graduation-eligibility logic in the Faker progression engine, Day 5, and later in Gold's graduation-rate calculation — defined once, here, not recomputed or re-guessed downstream).

**Loader:** `pipelines/common/config.py`, built around a deliberate two-stage validation split:

1. **Shape validation** (pydantic models, `extra="forbid"`) — catches malformed YAML, missing fields, wrong types, and invalid enum values (e.g., a typo'd `program_level`). This stage only knows about *one record at a time*.
2. **Relationship validation** (`load_reference_data`) — runs only after every record individually passes shape validation, and checks facts about the *whole set*: no duplicate `college_id`/`program_id`, and every program's `college_id` exists in `colleges.yaml`.

Splitting these two stages is what keeps error messages specific. A single combined validator would either have to run relationship checks on possibly-malformed data (crashing confusingly) or bury "this program points at a college that doesn't exist" inside a generic pydantic traceback. Instead:
- A malformed college entry raises `ConfigError: Invalid colleges config at ...` with the pydantic detail attached.
- An orphaned program raises `ConfigError: Program(s) reference unknown college_id ...: GHOST-1 -> 'NOPE'` — naming the exact program and the exact bad reference.

**Single exception type.** Callers catch `ConfigError` only — they don't need to know or care whether the root cause was a missing file, broken YAML syntax, a schema violation, or a cross-reference failure. That's an intentional interface decision: the caller's job is "handle config being broken," not "distinguish five different exception types that all mean the same thing to them."

**Tests:** `tests/unit/test_config.py` — 15 tests, deliberately weighted toward failure paths (missing file, malformed YAML, empty file, non-mapping YAML, missing required field, invalid enum, rejected unknown field, duplicate college/program IDs, orphaned college reference) rather than just the happy path. A config loader's entire value is catching bad input early with a clear message — so the failure paths *are* the product being tested, not an afterthought.

**Why validate config at startup:** the alternative — discovering a missing config value mid-pipeline, after Bronze has already been partially written — creates exactly the kind of half-completed state that idempotency rules are designed to prevent. Fail fast, fail at the boundary.

## 5. Logging Strategy

- Structured logging (JSON) via Python's `logging` + a JSON formatter — not `print()`.
- Every pipeline run gets a `batch_id` (UUID) that is attached to every log line and every audit column written that run, so logs and data can be cross-referenced.
- Log levels used deliberately: `INFO` for stage start/end and row counts, `WARNING` for recoverable data quality issues (e.g., a row failed validation and was quarantined), `ERROR` for failures that stop the pipeline.
- Logs written to `logs/` locally in the capstone; in a real deployment this would ship to a centralized log store (e.g., CloudWatch/ELK) — noted in Future Improvements.

## 6. Error Handling & Retry

- **Fail closed, not open**: if Silver validation fails for a batch, that batch does not proceed to Gold — it is quarantined, not silently dropped or silently passed through.
- Retries only for **transient** failures (network/connection errors) — using exponential backoff (e.g., `tenacity` library), capped at 3 attempts.
- **Non-transient failures** (bad schema, business rule violation) are not retried — retrying broken data just wastes compute and delays detection. These go to a quarantine table/folder with the reason logged.

## 7. Data Validation & Schema Validation

Two distinct concerns, handled separately:

| Type | Checked at | Tooling | Example |
|---|---|---|---|
| Schema validation | Bronze → Silver boundary | `pandera` / `pydantic` schemas | Column `student_id` must be non-null string |
| Data quality checks | Silver → Gold boundary | Great Expectations (or dbt tests) | `graduation_date >= enrollment_date` |

Schema validation asks "*is this shaped correctly?*"; data quality asks "*does this make business sense?*" — conflating them leads to either overly strict schemas (rejecting valid-but-messy data too early) or business rules with no schema guarantees underneath them.

## 8. Pipeline Monitoring & Metadata Tracking

- Every batch run writes a row to a `pipeline_run_log` table: `batch_id`, `stage`, `start_time`, `end_time`, `status`, `rows_in`, `rows_out`, `rows_quarantined`.
- This metadata table is itself queryable — "show me every batch that quarantined more than 5% of rows" is a one-line SQL query against `pipeline_run_log`, not a manual log-grepping exercise.

## 9. Pipeline Versioning & Idempotency

- Pipeline code is versioned via Git tags aligned to warehouse schema versions (e.g., pipeline `v1.2.0` ↔ warehouse migration `003_add_shifter_fact.sql`).
- **Idempotency rule**: every load is keyed by `(batch_id or semester_id, entity)` and uses `MERGE`/`UPSERT` semantics (or Bronze partition overwrite-by-partition), so re-running the same batch produces the same end state — never duplicate rows.
- Practically: Bronze partitions by `academic_year/semester/source_file`, and Silver/Gold use `MERGE INTO ... ON (natural_key) WHEN MATCHED ... WHEN NOT MATCHED ...`.

## 10. Incremental Loads

- Ingestion only picks up files not already recorded in `pipeline_run_log` for that `(academic_year, semester)` — this is the incremental-load "watermark."
- Silver/Gold transformations process only the Bronze partitions newer than the last successful Silver/Gold run (tracked via the same metadata table), not a full historical reprocess every time — except during an explicit, intentional backfill.

## 11. Backup Strategy

- Bronze is the backup — because it is immutable and append-only, it can always regenerate Silver/Gold from scratch (this is the entire point of keeping raw data).
- PostgreSQL warehouse: nightly `pg_dump` to a local backup volume (simulating what would be automated snapshots in a managed cloud DB).
- MinIO buckets: versioning enabled, so even Bronze files can't be silently overwritten without a recoverable prior version.

## 12. Testing Strategy (summary — detail in `13_Best_Practices.md`)

| Layer | Test type | Tool |
|---|---|---|
| Python transforms | Unit tests on pure functions | `pytest` |
| Bronze→Silver | Schema tests | `pandera` |
| Silver→Gold | Data quality tests | Great Expectations / dbt tests |
| dbt marts | `not_null`, `unique`, `relationships` tests | dbt native tests |
| End-to-end | Integration test on small fixture dataset | `pytest` + Docker test DB |

## 13. Bronze Ingestion — Implementation (Day 8)

**A real, sandbox-honest constraint:** MinIO and Postgres run in Docker (Day 2), and this development environment has no Docker daemon. Rather than fake this or skip it, the ingestion code is written against an interface (`ObjectStorage` in `pipelines/common/storage.py`), with two implementations:
- `LocalFileStorage` — filesystem-backed, used for actual development/testing here.
- `S3Storage` — real `boto3`-backed, MinIO/S3-API-compatible, tested against a **mocked** S3 backend (`moto`) rather than a live container. This proves the boto3 call logic is correct; a live MinIO smoke test is still something to run on your own machine once `docker compose up` works there.

Swapping backends is a one-line change at the call site (`ingest_all(storage=...)`) — ingestion logic itself never references a specific backend, which is the entire point of depending on the interface.

**Modules:** `pipelines/common/storage.py` (the interface + both backends), `pipelines/common/metadata.py` (the `pipeline_run_log` store, backed by DuckDB — a real, file-based, queryable database that needs no server, so it works identically here and once Postgres is live), `pipelines/ingestion/ingest_to_bronze.py` (the ingestion job), run via `python -m pipelines.ingestion.ingest_to_bronze`.

**What this stage does and doesn't do**, per `05_Medallion_Architecture.md`: file-level checks only (source exists, non-empty, expected columns present — `IngestionError` if not), audit-column stamping (`_ingested_at`, `_source_file`, `_batch_id`), and idempotent landing as Parquet. No per-field schema validation (Day 9) and no business-rule correctness (Day 11) happen here.

**Idempotency, proven, not assumed:** re-running `ingest_all()` without `force=True` checks `pipeline_run_log` for an existing `SUCCESS` row per `(entity, partition_key)` and skips it. Confirmed against the real dataset: first run wrote 34 files (2 reference + 1 student master + 31 semester-scoped entity files — one partition, 2021-1, correctly has no `graduation.csv` since no 1-year cert program can graduate in its very first observed semester); a second run produced **zero** new files, identical file set before and after. `force=True` intentionally bypasses this and appends a *new* batch-tagged file (Bronze never overwrites) — verified as a distinct, deliberate escape hatch for backfills, not an idempotency bug.

**Deferred, flagged explicitly (not silently skipped):** a full `pydantic-settings` `Settings` object for environment variables was flagged as a gap back in Day 3's review and is still not built. `load_minio_storage_from_env()` in `storage.py` is a narrowly-scoped helper for exactly the MinIO-connection-params need, not a substitute for that broader piece — still on the list.

**Testing:** `tests/unit/test_storage.py` (12 tests, parametrized across both `LocalFileStorage` and mocked `S3Storage` against the *same* behavioral contract — proving they're actually interchangeable, not just individually functional), `tests/unit/test_metadata.py` (6 tests on the idempotency check), `tests/unit/test_ingest_to_bronze.py` (8 tests against a small fixture population, covering the missing-entity-file path, audit-column presence, idempotent re-run, forced reprocessing, and file-level validation failures).

## 14. Bronze Schema Validation — Implementation (Day 9)

**Module:** `pipelines/common/schemas.py` — one `pandera` `DataFrameSchema` per Bronze entity (college, program, student, enrollment, graduation, dropout, shifter), wired into `ingest_to_bronze.py`'s `ingest_one()` as a post-write step (`_run_schema_validation`), logged to `pipeline_run_log` under a distinct stage (`bronze_schema_validation`) separate from ingestion itself.

**The design decision that actually matters here:** schema validation checks *shape* (right columns, right types, sane ranges), never *business vocabulary*. The clearest proof this was applied correctly, not just stated: `enrollment_status` has no `isin([...])` constraint, so all 9 of Day 6's intentionally noisy text variants (`' ENROLLED '`, `Enrolled`, `DROPPED OUT`, etc.) pass validation — confirmed by a parametrized test running every variant through the schema. Restricting that field to a controlled vocabulary at Bronze would have rejected the very realism Day 6 built on purpose; that normalization is Silver's job (Day 10), not Bronze's.

**Non-blocking by design.** A schema violation is *logged*, not used to reject the Bronze write — Bronze's entire purpose is preserving exactly what was received, even if malformed, so later root-causing is possible. Verified directly: a deliberately invalid row (`birth_year: 1850`) still landed in Bronze (`storage.exists(result["key"])` is `True`) while the validation report correctly logged `FAILED` with `birth_year` named in the error message. A quality gate that actually *blocks* promotion belongs at the Silver boundary (Day 11), not here.

**Confirmed against real data:** all 34 successfully-ingested Bronze partitions from the real 7,800-student dataset pass schema validation cleanly — zero violations on real (noisy-but-valid) data, and a hand-crafted malformed row (null `program_id`, invalid `program_level`, out-of-range `nominal_duration_years`) is caught with all three violations reported together in one pass (`lazy=True`), not just the first.

**Testing:** `tests/unit/test_schemas.py` — 27 tests: one valid-row pass per entity, one deliberately malformed variant per entity (null keys, invalid enums, out-of-range values, duplicate natural keys), a multi-violation collection check, and the noise-tolerance parametrized test across all 9 status variants. Plus 2 new tests in `test_ingest_to_bronze.py` confirming the wiring itself (not just the schemas in isolation) — that `ingest_one` actually calls validation, logs the result, and never lets a schema failure block the Bronze write.

---
*Next: `04_Data_Modeling.md` — the dimensional model in full.*
