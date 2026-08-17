# 12 — 30-Day Implementation Roadmap

> **Status: historical build log.** This was written as a forward-looking plan before the build started; the platform has since progressed well past several of these days (see `04_Data_Modeling.md`'s "Task 23/24" notes and `docs/16`/`17`/`18`/`19`/`20` for what was actually implemented, including corrections to what's outlined below). Keep this doc as a record of the original plan and process — useful for a capstone writeup — but treat `01`–`11` and `15`–`20` as the current source of truth for what the system actually does today, not this file.

## How to Use This Roadmap

Each day follows the same template: **Objectives → Concepts to Learn → Dev Tasks → Expected Output → Git Commit → Deliverables → Validation Checklist → Testing Checklist → Documentation Task.**

Work in short daily sessions (2–4 hrs realistic for a student balancing other coursework). If a day runs long, it's fine to spill into the next day's buffer — but do not skip validation/testing steps to "save time," since those are exactly the habits this roadmap is training.

> **Two scope corrections applied throughout this version:**
> 1. **Academic calendar:** all references to "8 semesters" / "2021–2024" (four single-year labels) have been corrected to **6 academic semesters across 2021-2022, 2022-2023, 2023-2024** (see `01_Project_Overview.md` §4).
> 2. **Ownership boundary:** Week 4 no longer builds dashboards. This repo's job ends at a published, tested `gold`/`marts` contract; a separate Web Team owns dashboards and consumes it read-only (see `01_Project_Overview.md` §8, `15_Tooling_Responsibility_Matrix.md`).

---

## WEEK 1 — Foundations: Architecture, Docker, Repository, Dummy Data

### Day 1 — Project Setup & Architecture Design
- **Objectives:** Stand up the repo skeleton; finalize architecture on paper before writing pipeline code.
- **Concepts:** Medallion architecture, layered system design, why architecture-first prevents rework.
- **Dev Tasks:** Create repo structure (`03_Data_Engineering.md` layout, no `dashboard/` folder); initialize Git; write `README.md` skeleton; draw the architecture diagram (`02_System_Architecture.md`), including the DE/DS ↔ Web Team consumption boundary, in your own words.
- **Expected Output:** Empty-but-structured repo pushed to GitHub.
- **Git Commit:** `chore: initialize repository structure`
- **Deliverables:** Repo skeleton, architecture diagram.
- **Validation Checklist:** [ ] All folders from `03_Data_Engineering.md` exist [ ] README explains project in 3 paragraphs, including the Web Team boundary.
- **Testing Checklist:** N/A (no code yet).
- **Documentation Task:** Draft `docs/01_Project_Overview.md` in your own words.

### Day 2 — Docker & Local Environment
- **Objectives:** Get PostgreSQL + MinIO running locally via Docker Compose.
- **Concepts:** Containers vs. VMs, Docker Compose networking, why containerize a capstone at all (reproducibility).
- **Dev Tasks:** Write `docker/docker-compose.yml` (Postgres, MinIO services); verify both start and are reachable; create `.env.example`.
- **Expected Output:** `docker compose up` brings up Postgres + MinIO successfully.
- **Git Commit:** `feat: add docker-compose for postgres and minio`
- **Deliverables:** Working `docker-compose.yml`, `.env.example`.
- **Validation Checklist:** [ ] `psql` can connect [ ] MinIO console loads at localhost.
- **Testing Checklist:** Manual smoke test: connect via CLI to both services.
- **Documentation Task:** Add "Local Setup" section to README.

### Day 3 — University Reference Data & Config
- **Objectives:** Encode colleges/programs/academic calendar as config, not code.
- **Concepts:** Config-driven design, why hardcoding reference data is a maintainability anti-pattern.
- **Dev Tasks:** Write `configs/colleges.yaml`, `configs/programs.yaml`, and **`configs/academic_calendar.yaml`** (3 academic years — `2021-2022`, `2022-2023`, `2023-2024` — 2 semesters each, plus the `year_level` domain `Freshman, Sophomore, Junior, Senior, Super Senior`); write a config-loader utility with validation (pydantic).
- **Expected Output:** Config files + loader module with unit test.
- **Git Commit:** `feat: add college/program/academic-calendar reference configs and loader`
- **Deliverables:** `configs/*.yaml`, `pipelines/common/config.py`.
- **Validation Checklist:** [ ] Every program maps to a valid college [ ] Every academic year maps to exactly 2 semesters [ ] Loader raises clear error on malformed YAML.
- **Testing Checklist:** `pytest tests/unit/test_config.py`.
- **Documentation Task:** Start `docs/03_Data_Engineering.md` config section.

### Day 4 — Faker Generator: Students & Reference Entities
- **Objectives:** Generate the static/reference and student master data.
- **Concepts:** Synthetic data realism, cohort modeling, latent variable simulation.
- **Dev Tasks:** Implement `data_generator/generators/generate_students.py`; generate cohorts for **academic years 2021-2022, 2022-2023, 2023-2024** (3 entering cohorts, not 4); assign latent risk profile per student.
- **Expected Output:** `student_master.csv` with realistic distributions.
- **Git Commit:** `feat: implement student master data generator`
- **Deliverables:** Generator script, sample output CSV.
- **Validation Checklist:** [ ] No duplicate `student_id` [ ] Gender/age distributions look plausible [ ] Exactly 3 cohorts generated.
- **Testing Checklist:** Unit test on generator's probability functions.
- **Documentation Task:** Begin `docs/08_Faker_Data_Generator.md`.

### Day 5 — Faker Generator: Progression Engine
- **Objectives:** Implement dropout/graduation/shifter logic across all five year levels.
- **Concepts:** Probabilistic state machines, survivorship/attrition curve realism.
- **Dev Tasks:** Implement `rules/progression_rules.py`; wire into `generate_progression.py`; run for the full **2021-2022 through 2023-2024** span (6 semesters).
- **Expected Output:** Enrollment, graduation, dropout, shifter CSVs per semester.
- **Git Commit:** `feat: implement student progression engine (dropout/graduation/shifter)`
- **Deliverables:** Full **6-semester** synthetic dataset.
- **Validation Checklist:** [ ] Every student has ≤1 terminal outcome [ ] Cohort totals reconcile (Section 8, `08_Faker_Data_Generator.md`) [ ] All five year levels (Freshman–Super Senior) appear in the output.
- **Testing Checklist:** Run the generator's self-check script; assert zero violations.
- **Documentation Task:** Document progression rules with the probability tables used.

### Day 6 — Noise Injection & Data Realism
- **Objectives:** Make the synthetic data messy on purpose.
- **Concepts:** Why "clean" test data hides real pipeline bugs.
- **Dev Tasks:** Implement `rules/noise_injection.py` (typos, dupes, late corrections) per `noise_rules.yaml`.
- **Expected Output:** Final noisy CSVs, ready to be treated as "source system extracts."
- **Git Commit:** `feat: add noise injection for realistic source data`
- **Deliverables:** Noisy dataset in `data_generator/output/`.
- **Validation Checklist:** [ ] Noise rates match config targets ±1% [ ] No noise breaks referential integrity of FKs.
- **Testing Checklist:** Unit test noise rate calculation.
- **Documentation Task:** Update Faker doc's noise section with actual observed rates.

### Day 7 — Week 1 Review & Buffer
- **Objectives:** Catch up, review Week 1 outputs, validate readiness for Bronze ingestion.
- **Concepts:** Retrospective review as an engineering habit.
- **Dev Tasks:** Code review your own Week 1 work; refactor anything rushed; confirm Docker + config + Faker output all work together end-to-end against the **corrected 6-semester academic calendar**.
- **Expected Output:** Clean, working foundation to build Bronze on top of.
- **Git Commit:** `chore: week 1 cleanup and review`
- **Deliverables:** Passing test suite, clean repo state.
- **Validation Checklist:** [ ] `docker compose up` works from a clean clone [ ] Faker output validates [ ] Every semester label follows `{academic_year}-{semester_number}` (e.g. `2022-2023-1`), never a bare year.
- **Testing Checklist:** Full `pytest` run, zero failures.
- **Documentation Task:** Write a short Week 1 retrospective note in `docs/14_Future_Improvements.md` (parking lot for ideas).

---

## WEEK 2 — Medallion Pipeline: Bronze, Silver, Gold

### Day 8 — Ingestion to Bronze
- **Objectives:** Land Faker output into Bronze with metadata.
- **Concepts:** Immutable landing zones, audit columns, incremental watermarking.
- **Dev Tasks:** Implement `pipelines/ingestion/`; write to MinIO as partitioned Parquet; stamp `_ingested_at`, `_batch_id`, `_source_file`.
- **Expected Output:** Bronze Parquet files in MinIO, per semester partition.
- **Git Commit:** `feat: implement batch ingestion to bronze layer`
- **Deliverables:** Ingestion job + `pipeline_run_log` table populated.
- **Validation Checklist:** [ ] Re-running ingestion doesn't duplicate partitions (idempotency) [ ] All **6 academic semesters** land correctly (3 academic years × 2 semesters — not 8).
- **Testing Checklist:** Integration test: run ingestion twice, assert identical row counts.
- **Documentation Task:** Write Bronze section of `docs/05_Medallion_Architecture.md`.

### Day 9 — Bronze Schema Validation
- **Objectives:** Add pandera schema checks at the Bronze read boundary.
- **Concepts:** Schema-on-read, fail-fast validation.
- **Dev Tasks:** Define pandera schemas per entity; wire into ingestion job as a post-write check; log violations.
- **Expected Output:** Schema validation report per batch.
- **Git Commit:** `feat: add schema validation for bronze tables`
- **Deliverables:** `pipelines/common/schemas.py`.
- **Validation Checklist:** [ ] Intentionally malformed test row is caught.
- **Testing Checklist:** Unit tests with valid + invalid fixture rows.
- **Documentation Task:** Document schema validation approach in `03_Data_Engineering.md`.

### Day 10 — Silver: Cleaning & Standardization
- **Objectives:** Build Silver cleaning transforms.
- **Concepts:** Data cleaning vs. business logic separation.
- **Dev Tasks:** Implement `pipelines/silver/clean_*.py` (whitespace, casing, status vocabulary mapping, **academic-year label normalization** — e.g. `"2022-23"` → `"2022-2023"`); use DuckDB SQL for transforms.
- **Expected Output:** Cleaned Silver Parquet/tables.
- **Git Commit:** `feat: implement silver cleaning transformations`
- **Deliverables:** Silver tables for all 7 entities.
- **Validation Checklist:** [ ] All status codes map to controlled vocabulary, none left as raw text [ ] All academic-year labels match `configs/academic_calendar.yaml` exactly.
- **Testing Checklist:** Unit tests per cleaning function with edge-case inputs.
- **Documentation Task:** Silver cleaning section of Medallion doc.

### Day 11 — Silver: Validation, Quarantine & Deduplication
- **Objectives:** Enforce business-correctness rules and dedupe.
- **Concepts:** Quarantine pattern, last-write-wins dedup, data quality gating.
- **Dev Tasks:** Implement business rule checks (e.g., graduation date ≥ enrollment date, **academic year is one of the 3 in-scope values**); write violators to `silver_quarantine_*`; implement dedup logic.
- **Expected Output:** Silver tables + quarantine tables with reasons logged.
- **Git Commit:** `feat: add silver validation, quarantine, and deduplication`
- **Deliverables:** Quarantine tables, dedup logic.
- **Validation Checklist:** [ ] Quarantine rate < 2% on synthetic data [ ] No duplicate natural keys in final Silver tables.
- **Testing Checklist:** Unit + integration tests with injected bad rows.
- **Documentation Task:** Document quarantine rate and business rules used.

### Day 12 — Gold: Dimension Tables
- **Objectives:** Build `dim_student` (SCD2), `dim_program`, `dim_college`, `dim_academic_period`, `dim_gender`, `dim_year_level`, `dim_calendar`.
- **Concepts:** Surrogate keys, SCD Type 1 vs Type 2 implementation.
- **Dev Tasks:** Write Gold dimension-build SQL/Python (DuckDB → Postgres `MERGE`); implement SCD2 logic for `dim_student`; populate `dim_academic_period` with exactly 6 rows (3 academic years × 2 semesters: `2021-2022` through `2023-2024`). (Task 23/24 replaced an earlier plan of a separate `dim_academic_year`/`dim_semester` snowflake pair with this single denormalized table — see `04_Data_Modeling.md` §2/§3.)
- **Expected Output:** Populated dimension tables in `gold` schema.
- **Git Commit:** `feat: build gold dimension tables with SCD logic`
- **Deliverables:** `warehouse/ddl/gold_dimensions.sql`, load scripts.
- **Validation Checklist:** [ ] Exactly one current row per student [ ] SCD2 history correctly closes prior rows on program change [ ] `dim_academic_period` has exactly 6 rows.
- **Testing Checklist:** Test SCD2 logic against a student with a simulated shift event.
- **Documentation Task:** Complete dimension section of `04_Data_Modeling.md`.

### Day 13 — Gold: Fact Tables
- **Objectives:** Build all fact tables (`fact_enrollment`, `fact_graduation`, `fact_dropout`, `fact_shifter`, `fact_retention`).
- **Concepts:** Fact grain, idempotent upserts.
- **Dev Tasks:** Implement fact-build scripts. At this data volume, facts are **fully rebuilt from Silver on every run** rather than incrementally merged (see `04_Data_Modeling.md` §9).
- **Expected Output:** Populated fact tables.
- **Git Commit:** `feat: build gold fact tables`
- **Deliverables:** `warehouse/ddl/gold_facts.sql`, load scripts.
- **Validation Checklist:** [ ] Row counts reconcile against Silver source counts [ ] Re-running the build doesn't duplicate.
- **Testing Checklist:** Idempotency test: run build twice, assert stable row count.
- **Documentation Task:** Fact table section of `04_Data_Modeling.md`.

### Day 14 — Gold: KPI Aggregation & Week 2 Review
- **Objectives:** Build `fact_institution_kpi` (Success Rate computation).
- **Concepts:** Weighted composite metrics, computing business KPIs once at the source.
- **Dev Tasks:** Implement Success Rate formula from `09_Data_Science.md` as a Gold aggregation job.
- **Expected Output:** `fact_institution_kpi` populated for all college × semester combinations — **8 colleges × 6 semesters = 48 rows.**
- **Git Commit:** `feat: compute institutional success rate in gold layer`
- **Deliverables:** KPI fact table.
- **Validation Checklist:** [ ] Weights sum to 1.0 [ ] Row count is exactly 48 [ ] Spot-check one college's manual calculation against the table.
- **Testing Checklist:** Unit test the weighted formula function against the worked example in `09_Data_Science.md`.
- **Documentation Task:** Week 2 retrospective; finalize `05_Medallion_Architecture.md`.

---

## WEEK 3 — Warehouse, Analytics Engineering, Machine Learning

### Day 15 — Warehouse Schema & Access Control
- **Objectives:** Finalize Postgres schema separation and role-based access, including the Web Team's read-only access.
- **Concepts:** Schema-per-layer, least-privilege access, external-consumer service boundaries.
- **Dev Tasks:** Create `bronze/silver/gold/marts/meta` schemas; create DB roles (`pipeline_writer`, `dbt_role`, **`web_service_reader`**); grant scoped permissions.
- **Expected Output:** Access-controlled warehouse.
- **Git Commit:** `feat: configure warehouse schemas and role-based access`
- **Deliverables:** `warehouse/ddl/roles_and_grants.sql`.
- **Validation Checklist:** [ ] `web_service_reader` role cannot write to any schema [ ] `web_service_reader` cannot read `silver`/`bronze`.
- **Testing Checklist:** Manual permission test per role.
- **Documentation Task:** Access model section of `06_Data_Warehouse.md`.

### Day 16 — dbt Project Setup & Staging Models
- **Objectives:** Initialize dbt, build staging models over Gold.
- **Concepts:** dbt project structure, staging vs. marts layering.
- **Dev Tasks:** `dbt init`; configure `profiles.yml`; build `stg_*` models as thin views over Gold tables.
- **Expected Output:** dbt project running `dbt run` successfully.
- **Git Commit:** `feat: initialize dbt project with staging models`
- **Deliverables:** `dbt/models/staging/*.sql`.
- **Validation Checklist:** [ ] `dbt run` succeeds with zero errors.
- **Testing Checklist:** `dbt test` on staging models (uniqueness/not-null).
- **Documentation Task:** dbt section of `07_Technology_Stack.md`.

### Day 17 — dbt Marts
- **Objectives:** Build the published consumption contract for the Web Team.
- **Concepts:** Semantic layer, documented SQL as a governance artifact, publishing a stable external interface.
- **Dev Tasks:** Build `mart_executive_summary`, `mart_college_performance`, `mart_program_performance`, `mart_institution_kpi`, `mart_retention_risk`.
- **Expected Output:** Marts queryable and matching Gold facts.
- **Git Commit:** `feat: add dbt marts for Web Team consumption`
- **Deliverables:** `dbt/models/marts/*.sql`, `schema.yml` with tests + docs.
- **Validation Checklist:** [ ] Every mart has `not_null`/`unique`/`relationships` tests defined.
- **Testing Checklist:** `dbt test` — all green.
- **Documentation Task:** Generate `dbt docs generate` site; link in README as the Web Team handoff artifact.

### Day 18 — Orchestration with Dagster
- **Objectives:** Wire the full pipeline into Dagster assets.
- **Concepts:** Asset-based orchestration, lineage graphs.
- **Dev Tasks:** Define Dagster assets for ingestion → Bronze → Silver → Gold → dbt run; set up a schedule (per simulated semester).
- **Expected Output:** Dagster UI showing the full asset lineage graph.
- **Git Commit:** `feat: orchestrate pipeline with dagster`
- **Deliverables:** `pipelines/dagster_assets.py` or equivalent.
- **Validation Checklist:** [ ] Full pipeline runs end-to-end from Dagster UI [ ] Lineage graph matches architecture diagram, including the consumption boundary.
- **Testing Checklist:** Trigger a manual run; verify all assets materialize successfully.
- **Documentation Task:** Orchestration section of `02_System_Architecture.md`.

### Day 19 — Feature Engineering for Forecasting
- **Objectives:** Build `ml_forecast_features`.
- **Concepts:** Lag/rolling/trend/seasonality feature design (see `10_Forecasting.md`).
- **Dev Tasks:** Implement feature-building SQL/Python job reading Gold facts, writing `ml_forecast_features`.
- **Expected Output:** Feature table, one row per entity per semester (6 semesters, not 8).
- **Git Commit:** `feat: build ml forecast feature table`
- **Deliverables:** `models/forecasting/build_features.py`.
- **Validation Checklist:** [ ] No feature leakage (lag features only use prior semesters) [ ] Feature table row count matches expected entity × 6-semester count.
- **Testing Checklist:** Unit test lag/rolling calculations against hand-computed examples.
- **Documentation Task:** Feature engineering section of `10_Forecasting.md` (already drafted — verify against implementation).

### Day 20 — Forecasting Model Training & Evaluation
- **Objectives:** Train Prophet per entity/metric; evaluate with walk-forward validation.
- **Concepts:** Time-series validation, baseline comparison discipline.
- **Dev Tasks:** Implement `models/forecasting/train_prophet.py`; implement the **3-fold** walk-forward evaluation harness (per `10_Forecasting.md` §5, corrected from the previous 4-fold design); compute MAE/RMSE/MAPE/R² vs. naive baseline.
- **Expected Output:** Evaluation report comparing Prophet vs. baselines per entity.
- **Git Commit:** `feat: train and evaluate prophet forecasting model`
- **Deliverables:** Trained model artifacts, evaluation report (CSV/Markdown).
- **Validation Checklist:** [ ] Prophet beats naive baseline on majority of series [ ] Series where it doesn't are explicitly flagged, not hidden [ ] The reduced fold count (3, not 4) and its effect on confidence is disclosed in the report.
- **Testing Checklist:** Unit test evaluation metric functions against known values.
- **Documentation Task:** Evaluation results section of `10_Forecasting.md`.

### Day 21 — Forecast Write-Back & Week 3 Review
- **Objectives:** Write forecasts to `fact_forecast`; review Week 3.
- **Concepts:** Treating ML output as just another versioned fact.
- **Dev Tasks:** Implement write-back job with `model_version` tagging; run full pipeline end-to-end via Dagster.
- **Expected Output:** `fact_forecast` populated, queryable like any other fact.
- **Git Commit:** `feat: write forecast output to warehouse`
- **Deliverables:** Populated `fact_forecast`.
- **Validation Checklist:** [ ] Forecast values are plausible (not negative enrollment, etc.) [ ] Full Dagster pipeline runs clean end-to-end.
- **Testing Checklist:** Full integration test of the entire pipeline, Bronze → Forecast.
- **Documentation Task:** Week 3 retrospective.

---

## WEEK 4 — Hardening, Testing, Web Team Handoff, Deployment, Documentation

> **Rewritten from the previous version.** The old Week 4 built Superset/Streamlit dashboards directly (Days 22–25). That work has been removed entirely — it belongs to the Web Team, in their own repository, on their own timeline. This repo's Week 4 instead hardens and **publishes** the service, and proves the handoff actually works.

### Day 22 — Web Team Handoff: Access & Contract Publication
- **Objectives:** Stand up the actual `web_service_reader` role end-to-end and publish the consumption contract.
- **Dev Tasks:** Verify `web_service_reader` grants (Day 15) against the real Postgres instance; run `dbt docs generate` and publish the docs site; finalize `11_Data_Consumption_Contract.md` against the real, built marts (not the design draft).
- **Git Commit:** `docs: publish web team data consumption contract`
- **Deliverables:** Working `web_service_reader` credentials, published dbt docs site, finalized `11_Data_Consumption_Contract.md`.
- **Validation Checklist:** [ ] A test connection using `web_service_reader` credentials can `SELECT` from `gold`/`marts` [ ] The same connection is denied on `silver`/`bronze` and denied all writes.
- **Testing Checklist:** Automated permission test (`tests/integration/test_web_service_reader_permissions.py`) — attempt a write and a `silver`/`bronze` read, assert both are rejected.
- **Documentation Task:** `11_Data_Consumption_Contract.md` finalized against real mart output.

### Day 23 — Mock Consumer Integration Test
- **Objectives:** Prove the contract is actually usable by an external consumer without needing the Web Team's real codebase.
- **Dev Tasks:** Write a small, throwaway script (not part of the production repo) that connects as `web_service_reader` and queries each published mart, asserting the shapes documented in Day 22 match reality.
- **Git Commit:** `test: add mock external-consumer integration check`
- **Deliverables:** `tests/integration/test_mock_web_consumer.py`.
- **Validation Checklist:** [ ] Every mart in the contract is queryable exactly as documented [ ] No mart requires business logic on the consumer side to be meaningful.
- **Testing Checklist:** Run the mock consumer script against a fresh `docker compose up` instance.
- **Documentation Task:** Note any contract gaps found and fix them before Day 24.

### Day 24 — Data Quality Hardening
- **Objectives:** Add Great Expectations / dbt test coverage gaps.
- **Dev Tasks:** Review all layers for missing tests; add any missing `not_null`/`relationships`/business-rule checks; specifically confirm the academic-calendar validation rules (`check_academic_year_in_scope`, `05_Medallion_Architecture.md`) have full test coverage.
- **Git Commit:** `test: harden data quality coverage across pipeline`
- **Deliverables:** Full test coverage report.
- **Validation Checklist:** [ ] Every fact/dimension table has at least `not_null` + `unique` + `relationships` tests.
- **Testing Checklist:** `dbt test` + `pytest` full suite, zero failures.
- **Documentation Task:** Data quality section of `13_Best_Practices.md`.

### Day 25 — Integration Testing & Idempotency Proof
- **Objectives:** Prove the pipeline is idempotent and recoverable end-to-end.
- **Dev Tasks:** Write an integration test that runs the full pipeline twice on the same batch and asserts identical output; simulate a mid-pipeline failure and recovery.
- **Git Commit:** `test: add end-to-end idempotency and recovery tests`
- **Deliverables:** `tests/integration/test_full_pipeline.py`.
- **Validation Checklist:** [ ] Double-run produces identical row counts everywhere, including the corrected `fact_institution_kpi` (48 rows) [ ] Simulated failure recovers cleanly on re-run.
- **Testing Checklist:** CI-style full run in a clean Docker environment.
- **Documentation Task:** Testing section of `13_Best_Practices.md`.

### Day 26 — Deployment Packaging
- **Objectives:** Make the whole project runnable from a clean clone.
- **Dev Tasks:** Finalize `docker-compose.yml` for all DE/DS services (Postgres, MinIO, Dagster) — **no dashboard service included**; write a `Makefile` or `setup.sh` for one-command bootstrap.
- **Git Commit:** `feat: finalize deployment packaging`
- **Deliverables:** One-command bootstrap from clean clone.
- **Validation Checklist:** [ ] Fresh clone + `docker compose up` + one setup script reproduces the entire working system [ ] `web_service_reader` credentials are printed/documented as part of setup output, ready to hand to the Web Team.
- **Testing Checklist:** Test on a clean machine/VM if possible.
- **Documentation Task:** Finalize "Getting Started" in README, including a "Handing off to the Web Team" subsection.

### Day 27 — Documentation Completion
- **Objectives:** Finalize all 15 documentation files.
- **Dev Tasks:** Proofread and complete `docs/01`–`docs/15`; ensure every Mermaid diagram renders; ensure every design decision has a stated rationale; confirm every "STALE — pending regeneration" flag left by the academic-calendar migration has actually been resolved with real, re-verified numbers.
- **Git Commit:** `docs: finalize full documentation set`
- **Deliverables:** Complete `docs/` folder, zero remaining STALE flags.
- **Validation Checklist:** [ ] Every doc file has no placeholder/TODO/STALE text left [ ] All diagrams render correctly on GitHub.
- **Testing Checklist:** N/A.
- **Documentation Task:** This is the task.

### Day 28 — Web Team Dry-Run
- **Objectives:** Simulate an actual handoff, not just a permissions test.
- **Dev Tasks:** If possible, have someone unfamiliar with this repo (a classmate, or the Web Team if they exist by this point) connect with `web_service_reader` and try to answer 2–3 of `01_Project_Overview.md`'s defense questions using only the published marts and `11_Data_Consumption_Contract.md` — no access to this repo's source code.
- **Git Commit:** `docs: incorporate feedback from web team dry-run`
- **Deliverables:** A short writeup of what was confusing or missing in the contract, and the fixes made in response.
- **Validation Checklist:** [ ] A third party can answer at least 2 defense-question-style queries using only `gold`/`marts` and the published docs.
- **Testing Checklist:** N/A — this is a usability check, not an automated test.
- **Documentation Task:** Update `11_Data_Consumption_Contract.md` with anything the dry-run revealed was missing.

### Day 29 — Final Review, Demo Rehearsal
- **Objectives:** Rehearse the capstone defense; final polish.
- **Dev Tasks:** Run the full system from scratch one final time; rehearse answering the six questions in `01_Project_Overview.md` §10 (including the new service-boundary question); record a demo walkthrough if required.
- **Git Commit:** `chore: final polish before submission`
- **Deliverables:** Submission-ready repository + demo.
- **Validation Checklist:** [ ] Full pipeline runs clean from a fresh clone [ ] All six defense questions can be answered live, with the system.
- **Testing Checklist:** Full regression run — everything green.
- **Documentation Task:** Final README pass.

### Day 30 — Submission
- **Objectives:** Ship it.
- **Dev Tasks:** Tag the release; confirm the repo is clean and reproducible one more time.
- **Git Commit:** `chore: tag v1.0.0`
- **Deliverables:** Tagged, submission-ready repository.
- **Validation Checklist:** [ ] Fresh clone + `docker compose up` reproduces the entire working DE/DS system, with no dashboard build step, from a clean environment.
- **Testing Checklist:** Full regression run — everything green.
- **Documentation Task:** Tag release `v1.0.0`.

---
*Next: `13_Best_Practices.md` — engineering practices reference.*