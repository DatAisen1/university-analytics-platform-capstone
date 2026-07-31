# 05 — Medallion Architecture (Bronze / Silver / Gold)

## 1. Purpose of Each Layer, In One Sentence

- **Bronze**: preserve exactly what was received.
- **Silver**: make it correct and consistent.
- **Gold**: make it useful and fast to query.

Conflating these (e.g., cleaning data *while* ingesting it) is the single most common mistake in pipelines that later become impossible to debug — you lose the ability to distinguish "the source sent bad data" from "our cleaning logic has a bug."

---

## 2. Bronze Layer

### Purpose
Immutable, append-only landing zone. Bronze is a faithful copy of the source, plus metadata — never transformed, never overwritten, never deleted.

### Tables
`bronze_sis_student`, `bronze_sis_enrollment`, `bronze_sis_program`, `bronze_sis_college`, `bronze_sis_graduation`, `bronze_sis_dropout`, `bronze_sis_shifter` — one Bronze table per source entity, mirroring the Faker generator's output files.

### Storage
Parquet files in MinIO, partitioned by `academic_year=/semester=/ingested_date=`. Parquet chosen over CSV for Bronze because it preserves types (no re-inferring "was this column a string or an int?") and is far more efficient for the repeated reads Silver processing will do.

### Metadata / Audit Columns (added at ingestion, nothing else changes)
`_ingested_at`, `_source_file`, `_batch_id`, `_row_hash` (for change detection on reprocessing).

### Versioning
MinIO bucket versioning enabled — even Bronze writes are never silently destructive. Combined with partitioning by ingestion date, this means "what did Bronze look like on date X" is always answerable.

### Incremental Loading
Ingestion job checks `pipeline_run_log` for the last successfully ingested `(academic_year, semester, source_file)` combination and only pulls new/unprocessed files — a new semester's file is a new partition, not a rewrite of history.

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
- Map raw status codes/free-text (as they'd realistically appear from a registrar export) to a controlled vocabulary (`ENROLLED`, `GRADUATED`, `DROPPED`, `SHIFTED`, `ON_LEAVE`).
- Standardize college/program names against the `configs/colleges.yaml` and `configs/programs.yaml` reference lists (catches typos/variants like "BS IT" vs "BSIT").

### Business Rules (data-correctness rules, not KPI formulas)
- A student cannot be `GRADUATED` in a semester before their `enrollment_date`.
- A student cannot have two conflicting statuses in the same semester (e.g., both `DROPPED` and `ENROLLED`).
- Rows violating these are **quarantined** (written to `silver_quarantine_<entity>` with the failure reason), not silently dropped — so a data engineer can inspect and decide whether it's a source bug or a real edge case.

### Data Quality Checks
Implemented as Great Expectations suites (or dbt tests, see `13_Best_Practices.md`) run as a gate: Silver → Gold promotion only happens if the batch passes a defined quality threshold (e.g., <1% quarantine rate). Above that threshold, the batch is held and alerted on rather than silently propagated.

### Deduplication
Keyed on natural key + semester (`student_id + semester_id` for enrollment records) — last-write-wins based on `_ingested_at`, since a later file for the same semester represents a correction, not a duplicate to discard arbitrarily.

### Normalization
Silver tables are still close to source shape (one table per entity) but fully typed, deduplicated, and validated — **not yet dimensionally modeled**. That transformation is Gold's job. Keeping this separate means Silver can be validated against source-system business rules independently of warehouse modeling decisions.

### Tables
`silver_student`, `silver_enrollment`, `silver_program`, `silver_college`, `silver_graduation`, `silver_dropout`, `silver_shifter`.

### Implementation Notes — Cleaning (Day 10)

**Modules:** `pipelines/silver/cleaning_rules.py` (pure functions) + `pipelines/silver/clean_entities.py` (DuckDB SQL orchestration, per `07_Technology_Stack.md`'s "DuckDB SQL for the actual Bronze→Silver→Gold transformations" decision), run via `python -m pipelines.silver.clean_entities`.

**One correction to the original design, made honest here rather than silently reconciled:** this section originally said the controlled vocabulary was `ENROLLED, GRADUATED, DROPPED, SHIFTED, ON_LEAVE`. The actual generator (Days 4–6) never produces `SHIFTED` or `ON_LEAVE` as an `enrollment_status` value — a shift is its own event type (`fact_shifter`), and leave-of-absence was never modeled. The real controlled vocabulary Silver normalizes to is `ENROLLED`, `GRADUATED`, `DROPPED` — three values, confirmed against the actual cleaned output.

**Global read, not per-partition.** `read_all_bronze()` unions *every* Bronze Parquet file for an entity — across all 8 semester partitions and every ingestion batch — into one DataFrame before cleaning. This is deliberate: Day 11's dedup step needs the full cross-partition picture (a late correction physically lands in a *later* partition's file but describes an *earlier* semester), so Silver has to think of each entity as one logical table, not eight partition-shaped ones, even though Bronze physically stores it that way.

**`normalize_enrollment_status` resolves all 9 of Day 6's real noise variants** (`ENROLLED`, `Enrolled`, `enrolled`, `' ENROLLED '`, `GRADUATED`, `Graduated`, `DROPPED`, `Dropped`, `DROPPED OUT`) down to exactly 3 controlled values — confirmed against the real 33,800-row dataset: `ENROLLED` (31,519), `DROPPED` (1,295), `GRADUATED` (986), zero rows left unmapped. A non-raising variant (`normalize_enrollment_status_safe`) tags anything genuinely unrecognized as `'UNKNOWN:<raw>'` rather than raising — cleaning's job is to normalize what it can and surface what it can't, not to reject rows. Rejection is Day 11's quarantine step, a deliberately separate concern.

**No typo correction on `home_province`.** Day 6 also injected typo noise there, but unlike `enrollment_status` (a small closed set), free-text place names have no equally-clean closed-form correction without a much larger reference/fuzzy-matching effort — correctly scoped out rather than half-implemented. Only whitespace trimming is applied.

**Testing:** `tests/unit/test_cleaning_rules.py` (22 tests — all 9 real noise variants, plus edge cases the real data doesn't happen to contain: `None`, empty string, whitespace-only, and unrecognized values) and `tests/unit/test_clean_entities.py` (6 tests proving the DuckDB orchestration itself — not just the pure functions — including the cross-partition global-read behavior and that unrecognized status values survive as `UNKNOWN:` rather than vanishing).

### Implementation Notes — Validation, Quarantine & Dedup (Day 11)

**Module:** `pipelines/silver/validate_and_dedupe.py`, run via `python -m pipelines.silver.validate_and_dedupe`.

**Order matters: dedup runs before business-rule checks, not after.** A late correction's entire point is that the newer version should be trusted — checking a since-superseded earlier version for correctness would be validating the wrong thing. Dedup picks the winner per natural key first (last-write-wins by `_ingested_at`, via a DuckDB `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY _ingested_at DESC)`), and only the survivors go through business-rule checks.

**Dedup result, confirmed against real data — the single most satisfying check in this whole project so far:** 33,800 noisy Bronze-derived rows → 1,099 duplicates dropped (298 in-partition duplicates + 801 late corrections, exactly matching Day 6's injected counts) → **32,701 final rows, an exact match to Day 5's pre-noise ground truth.** The round trip — clean generation → intentional noise injection → Bronze → Silver dedup — reconstructs the original count exactly. Zero duplicate natural keys remain in the final table (verified directly, not assumed).

**Three business rules, quarantine-not-reject:**
1. `check_known_status` — rejects any row Day 10 tagged `'UNKNOWN:...'` (closes the loop between the two days' deliberately separated concerns).
2. `check_semester_within_cohort_range` — a cross-entity check against Silver's `student` table: an enrollment record's `(academic_year, semester_number)` must fall within the student's valid observed window (not before cohort entry, not after 2024-2).
3. `check_dropout_consistency` — a cross-entity check against `dropout`: a `DROPPED` enrollment row must have a matching dropout event, and vice versa.

**Honest result on the real dataset: 0% quarantine rate, and that's the correct outcome, not a weak test.** Because the synthetic generator (Days 4–6) constructs enrollment/dropout/graduation records together and correctly by design, none of the three business rules should ever find a real violation — that's confirmation the earlier stages were built correctly, not evidence the checks are decorative. Their value is proven a different way: **14 tests with deliberately injected bad rows** (a record dated before its student's cohort entry, a `DROPPED` status with no matching dropout event, a still-`UNKNOWN` status) confirm each rule actually quarantines what it claims to, with the reason recorded in `_quarantine_reason` — the quarantine mechanism is proven by construction of bad input, not by hoping the real data eventually breaks something.

**Testing:** `tests/unit/test_validate_and_dedupe.py` — 14 tests: dedup correctness (exact duplicates, late-correction winner selection, distinct-key preservation), each business rule in isolation with an injected violation, and a full `process_enrollment` integration test against a fixture containing one of every problem type at once, confirming the final counts (`rows_in=6 → duplicates_dropped=1, quarantined=3, rows_out=2`) add up exactly.

---

## 4. Gold Layer

### Purpose
Business-ready, dimensionally modeled, aggregated, and ML-ready. Everything downstream (dashboards, ML, ad-hoc analytics) reads only from here.

### Business-Ready Datasets
The full star schema from `04_Data_Modeling.md`: `dim_student`, `dim_program`, `dim_college`, `dim_semester`, `dim_academic_year`, `dim_calendar`, and all `fact_*` tables.

### Aggregations
`fact_institution_kpi` is a pre-aggregated fact — computed once per Gold run, not recomputed ad hoc by every dashboard query. This guarantees the dashboard, dbt marts, and any analyst querying directly all see the *same* success rate number, because it's computed in exactly one place.

### ML Datasets
A dedicated Gold table, `ml_forecast_features` (see `10_Forecasting.md` for feature detail), built as a time-series-shaped table: one row per `(college/program, semester)` with lag features, rolling averages, and the target variable — decoupled from the dashboard-facing star schema so ML feature engineering can evolve independently.

### Dashboard Datasets
Gold facts are queried directly by dbt marts (`mart_executive_summary`, `mart_college_performance`, etc. — see `11_Dashboard.md`), which the dashboard tool queries. The dashboard itself never touches Silver or Bronze.

### KPI Tables
`fact_institution_kpi` (per college/semester) plus a rolled-up `fact_institution_kpi_overall` (per semester, campus-wide) for the Executive dashboard's top-line number.

### Decision-Support Tables
`mart_retention_risk` — a dbt mart flagging programs whose retention rate has declined for 2+ consecutive semesters, directly supporting the "where should we intervene" administrative decision.

---

## 4.1 Gold Implementation Summary (Days 12–14)

All of Section 4's abstract Gold design is now real, built, and tested end-to-end against the actual 7,800-student dataset:

| Deliverable | Module | Real result |
|---|---|---|
| Dimensions (incl. `dim_student` SCD2) | `pipelines/gold/build_dimensions.py` | 8,012 `dim_student` rows for 7,800 students; a real SCD2 bug (entry-semester shifts) found and fixed — see `04_Data_Modeling.md` §9 |
| Fact tables | `pipelines/gold/build_facts.py` | All 5 facts reconcile exactly to Silver source counts; AS-OF join against SCD2 proven time-aware, not just student-aware — see `04_Data_Modeling.md` §10 |
| `fact_institution_kpi` (Success Rate) | `pipelines/gold/build_kpi.py` | 64 rows (8 colleges × 8 semesters); formula matches `09_Data_Science.md`'s worked example exactly; a real bug (missing `college_key` on shift events) found and fixed — see `09_Data_Science.md` §7 |

Every one of these ran against a genuine constraint worth restating here: Postgres isn't running in this environment (no Docker daemon — Day 2's note). Gold lands as Parquet in `warehouse/gold_store/` via the same `ObjectStorage` abstraction used for Bronze and Silver. Materializing into the real Postgres warehouse is Week 3's job (Day 15 onward); the transformation logic itself doesn't change, only the write target.

## 5. Layer Promotion Rule (Summary Table)

| Transition | Gate | On failure |
|---|---|---|
| Source → Bronze | File exists, non-empty, expected columns present | Alert, do not ingest |
| Bronze → Silver | Schema validation (pandera) | Quarantine row, continue batch |
| Silver → Gold | Data quality suite pass rate ≥ threshold | Hold entire batch, alert, do not promote |
| Gold → Warehouse/dbt | dbt tests (`not_null`, `unique`, `relationships`) | Fail dbt run, block dashboard refresh |

This "gate at every boundary" pattern is what makes the phrase "the data is trustworthy" actually verifiable rather than aspirational.

---
*Next: `06_Data_Warehouse.md` — physical warehouse design in PostgreSQL.*
