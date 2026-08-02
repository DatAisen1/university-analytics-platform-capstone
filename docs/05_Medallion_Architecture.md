# 05 — Medallion Architecture (Bronze / Silver / Gold)

## 1. Purpose of Each Layer, In One Sentence

- **Bronze**: preserve exactly what was received.
- **Silver**: make it correct and consistent.
- **Gold**: make it useful and fast to query — and the last layer this repo is responsible for.

Conflating these (e.g., cleaning data *while* ingesting it) is the single most common mistake in pipelines that later become impossible to debug — you lose the ability to distinguish "the source sent bad data" from "our cleaning logic has a bug."

---

## 2. Bronze Layer

### Purpose
Immutable, append-only landing zone. Bronze is a faithful copy of the source, plus metadata — never transformed, never overwritten, never deleted.

### Tables
`bronze_sis_student`, `bronze_sis_enrollment`, `bronze_sis_program`, `bronze_sis_college`, `bronze_sis_graduation`, `bronze_sis_dropout`, `bronze_sis_shifter` — one Bronze table per source entity, mirroring the Faker generator's output files.

### Storage
Parquet files in MinIO, partitioned by `academic_year=<label>/semester=<1|2>/ingested_date=`, where `<label>` is the real school-year label (`2021-2022`, `2022-2023`, `2023-2024`) — **not** a bare single year. Parquet chosen over CSV for Bronze because it preserves types and is far more efficient for the repeated reads Silver processing will do.

### Metadata / Audit Columns (added at ingestion, nothing else changes)
`_ingested_at`, `_source_file`, `_batch_id`, `_row_hash` (for change detection on reprocessing).

### Versioning
MinIO bucket versioning enabled — even Bronze writes are never silently destructive. Combined with partitioning by ingestion date, this means "what did Bronze look like on date X" is always answerable.

### Incremental Loading
Ingestion job checks `pipeline_run_log` for the last successfully ingested `(academic_year, semester, source_file)` combination and only pulls new/unprocessed files — a new semester's file is a new partition, not a rewrite of history. Across the full scope, that's **6 semester partitions per entity** (3 academic years × 2 semesters), not 8.

### What Bronze Explicitly Does NOT Do
- No deduplication
- No type coercion beyond what's needed to store as Parquet
- No filtering of "bad" rows
- No business logic of any kind

---

## 3. Silver Layer

### Purpose
Turn "what we received" into "what we can trust." This is where data quality is enforced.

### Cleaning
- Trim whitespace, standardize casing (e.g., program names), null-normalize empty strings to actual NULLs.
- Standardize date formats to ISO 8601.

### Validation (Schema)
Enforced via `pandera` schemas per entity — e.g., `student_id` non-null and unique, `enrollment_status` in a controlled enum, `birth_year` within a sane range (1980–2010 given traditional-age students).

### Transformation
- Map raw status codes/free-text (as they'd realistically appear from a registrar export) to a controlled vocabulary (`ENROLLED`, `GRADUATED`, `DROPPED`).
- Standardize college/program names against the `configs/colleges.yaml` and `configs/programs.yaml` reference lists (catches typos/variants like "BS IT" vs "BSIT").
- Normalize `academic_year` text variants (e.g., a source file that writes `"2022-23"` or `"AY 2022-2023"`) to the canonical `configs/academic_calendar.yaml` label (`2022-2023`) — a new normalization step made necessary specifically by moving to real split-year labels instead of a bare single year.

### Business Rules (data-correctness rules, not KPI formulas)
- A student cannot be `GRADUATED` in a semester before their `enrollment_date`.
- A student cannot have two conflicting statuses in the same semester (e.g., both `DROPPED` and `ENROLLED`).
- An enrollment record's `(academic_year, semester_number)` must be one of the 6 valid in-scope combinations — anything else (a typo'd or out-of-range academic year) is quarantined, not silently accepted.
- Rows violating these are **quarantined** (written to `silver_quarantine_<entity>` with the failure reason), not silently dropped — so a data engineer can inspect and decide whether it's a source bug or a real edge case.

### Data Quality Checks
Implemented as Great Expectations suites (or dbt tests, see `13_Best_Practices.md`) run as a gate: Silver → Gold promotion only happens if the batch passes a defined quality threshold (e.g., <1% quarantine rate). Above that threshold, the batch is held and alerted on rather than silently propagated.

### Deduplication
Keyed on natural key + semester (`student_id + semester_id` for enrollment records) — last-write-wins based on `_ingested_at`, since a later file for the same semester represents a correction, not a duplicate to discard arbitrarily.

### Normalization
Silver tables are still close to source shape (one table per entity) but fully typed, deduplicated, and validated — **not yet dimensionally modeled**. That transformation is Gold's job. Keeping this separate means Silver can be validated against source-system business rules independently of warehouse modeling decisions.

### Tables
`silver_student`, `silver_enrollment`, `silver_program`, `silver_college`, `silver_graduation`, `silver_dropout`, `silver_shifter`.

### Implementation Notes — Cleaning

> **⚠️ STALE — pending regeneration.** Prior concrete counts here (e.g., "33,800 rows checked, `ENROLLED` (31,519), `DROPPED` (1,295), `GRADUATED` (986)") were measured against the old 8-semester dataset and are no longer accurate; they must be remeasured after the generator and pipeline are re-run against the 6-semester grain. The design decisions below are unaffected and carry forward.

**Modules:** `pipelines/silver/cleaning_rules.py` (pure functions) + `pipelines/silver/clean_entities.py` (DuckDB SQL orchestration), run via `python -m pipelines.silver.clean_entities`.

**The real controlled vocabulary Silver normalizes `enrollment_status` to is `ENROLLED`, `GRADUATED`, `DROPPED`** — three values. A shift is its own event type (`fact_shifter`), and leave-of-absence is not modeled, so `SHIFTED`/`ON_LEAVE` never appear as `enrollment_status` values.

**Global read, not per-partition.** `read_all_bronze()` unions *every* Bronze Parquet file for an entity — across all 6 in-scope semester partitions (previously mis-stated as 8) and every ingestion batch — into one DataFrame before cleaning. This is deliberate: dedup needs the full cross-partition picture (a late correction physically lands in a *later* partition's file but describes an *earlier* semester), so Silver has to think of each entity as one logical table, not partition-shaped ones.

**No typo correction on `home_province`.** Free-text place names have no equally-clean closed-form correction without a much larger reference/fuzzy-matching effort — correctly scoped out rather than half-implemented. Only whitespace trimming is applied.

**Testing:** `tests/unit/test_cleaning_rules.py` and `tests/unit/test_clean_entities.py` keep their structure and noise-variant coverage; fixture semester labels need updating to the `{academic_year}-{semester_number}` format.

### Implementation Notes — Validation, Quarantine & Dedup

> **⚠️ STALE — pending regeneration.** Prior concrete counts (33,800 → 32,701 rows, specific quarantine tallies) were measured against the old 8-semester dataset and must be remeasured.

**Module:** `pipelines/silver/validate_and_dedupe.py`, run via `python -m pipelines.silver.validate_and_dedupe`.

**Order matters: dedup runs before business-rule checks, not after.** A late correction's entire point is that the newer version should be trusted — checking a since-superseded earlier version for correctness would be validating the wrong thing. Dedup picks the winner per natural key first (last-write-wins by `_ingested_at`, via a DuckDB `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY _ingested_at DESC)`), and only the survivors go through business-rule checks.

**Four business rules, quarantine-not-reject** (a new one added for the academic-calendar correction):
1. `check_known_status` — rejects any row tagged `'UNKNOWN:...'` by the cleaning stage.
2. `check_semester_within_cohort_range` — a cross-entity check against Silver's `student` table: an enrollment record's `(academic_year, semester_number)` must fall within the student's valid observed window.
3. `check_dropout_consistency` — a cross-entity check against `dropout`: a `DROPPED` enrollment row must have a matching dropout event, and vice versa.
4. `check_academic_year_in_scope` (new) — an enrollment record's `academic_year` must be one of `2021-2022`, `2022-2023`, `2023-2024` per `configs/academic_calendar.yaml`; anything else is quarantined rather than silently coerced. This rule specifically exists because the academic-year label format changed from a single year to a school-year string, which is exactly the kind of change that should be enforced at the validation boundary, not assumed to have propagated everywhere correctly.

**Testing:** `tests/unit/test_validate_and_dedupe.py` keeps its structure (dedup correctness, each business rule tested in isolation with an injected violation, a full integration test); add one new test for rule 4 above, and update all fixture counts once the pipeline is re-run.

---

## 4. Gold Layer

### Purpose
Business-ready, dimensionally modeled, aggregated, and ML-ready. This is the **last layer this repo owns** — everything downstream (Web Team, ML feature consumers, ad-hoc analytics) reads only from here, and only via `gold`/`marts`.

### Business-Ready Datasets
The full star schema from `04_Data_Modeling.md`: `dim_student`, `dim_program`, `dim_college`, `dim_semester`, `dim_academic_year`, `dim_year_level`, `dim_calendar`, and all `fact_*` tables.

### Aggregations
`fact_institution_kpi` is a pre-aggregated fact — computed once per Gold run, not recomputed ad hoc by every consumer's query. This guarantees the Web Team's dashboard, dbt marts, and any analyst querying directly all see the *same* success rate number, because it's computed in exactly one place.

### ML Datasets
A dedicated Gold table, `ml_forecast_features` (see `10_Forecasting.md` for feature detail), built as a time-series-shaped table: one row per `(college/program, semester)` with lag features, rolling averages, and the target variable — decoupled from the consumption-facing star schema so ML feature engineering can evolve independently.

### Consumption Datasets (formerly "Dashboard Datasets")
Gold facts are queried directly by dbt marts (`mart_executive_summary`, `mart_college_performance`, etc. — see `11_Data_Consumption_Contract.md`), which the **Web Team's** service queries via the read-only `web_service_reader` role. This repo never builds or operates the presentation layer itself; it publishes the marts and stops there.

### KPI Tables
`fact_institution_kpi` (per college/semester) plus a rolled-up `fact_institution_kpi_overall` (per semester, campus-wide) for the top-line institutional number, however the Web Team chooses to display it.

### Decision-Support Tables
`mart_retention_risk` — a dbt mart flagging programs whose retention rate has declined for 2+ consecutive semesters, directly supporting the "where should we intervene" administrative decision.

---

## 4.1 Gold Implementation Summary

> **⚠️ STALE — pending regeneration.** The prior version of this table reported real results (8,012 `dim_student` rows for 7,800 students, 64-row `fact_institution_kpi`) from a run against the old 8-semester model. Expected shapes under the corrected 6-semester model:

| Deliverable | Module | Expected shape (6-semester model) |
|---|---|---|
| Dimensions (incl. `dim_student` SCD2) | `pipelines/gold/build_dimensions.py` | One current row per student, plus one additional row per shifter — exact totals depend on regenerated data |
| Fact tables | `pipelines/gold/build_facts.py` | Row counts must reconcile exactly to Silver source counts (unchanged requirement) |
| `fact_institution_kpi` (Success Rate) | `pipelines/gold/build_kpi.py` | **8 colleges × 6 semesters = 48 rows** (was 64 under the incorrect model) |

Every one of these runs against a genuine, unrelated constraint: Postgres isn't running in this environment (no Docker daemon). Gold lands as Parquet in `warehouse/gold_store/` via the same `ObjectStorage` abstraction used for Bronze and Silver. Materializing into the real Postgres warehouse only changes the write target, not the transformation logic.

## 5. Layer Promotion Rule (Summary Table)

| Transition | Gate | On failure |
|---|---|---|
| Source → Bronze | File exists, non-empty, expected columns present | Alert, do not ingest |
| Bronze → Silver | Schema validation (pandera) | Quarantine row, continue batch |
| Silver → Gold | Data quality suite pass rate ≥ threshold | Hold entire batch, alert, do not promote |
| Gold → Warehouse/dbt marts | dbt tests (`not_null`, `unique`, `relationships`) | Fail dbt run, block marts publication (and therefore block the Web Team's read) |

This "gate at every boundary" pattern is what makes the phrase "the data is trustworthy" actually verifiable rather than aspirational.

---
*Next: `06_Data_Warehouse.md` — physical warehouse design in PostgreSQL.*