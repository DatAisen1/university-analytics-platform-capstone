# University Academic Analytics and Forecasting System
### NEUST Sumacab Campus — Institutional Success Rate Platform (Data Engineering + Data Science Service)

A production-inspired data engineering + analytics + forecasting platform, designed as a one-month university capstone but architected the way a real enterprise data platform would be — layered, governed, tested, and reproducible.

> **Ownership boundary:** This repository is the **Data Engineering + Data Science service** only. Dashboards, UI, and authentication belong to a separate **Web Team**, who consume this service's outputs read-only. See `docs/01_Project_Overview.md` §8 and `docs/15_Tooling_Responsibility_Matrix.md`.

## What This Is

Not a dashboard. A **data engineering platform**: batch ingestion → Bronze/Silver/Gold medallion pipeline → dimensional warehouse → analytics engineering (dbt) → forecasting (Prophet) → a **published, read-only consumption contract**, built entirely on free, open-source, self-hosted tools (`docker compose up` and nothing else required).

## Academic Period Model

This project covers **3 academic years — `2021-2022`, `2022-2023`, `2023-2024`** — with a 1st and 2nd Semester each, for **6 academic semesters total**, and the full student year-level progression (`Freshman → Sophomore → Junior → Senior → Super Senior → Graduate`). See `docs/01_Project_Overview.md` §4 for the authoritative definition and the migration note explaining an earlier, incorrect 8-semester model that has since been corrected throughout this documentation set.

## Documentation Index

| Doc | Contents |
|---|---|
| [01 — Project Overview](01_Project_Overview.md) | Problem framing, objectives, scope boundaries, academic period model, success criteria |
| [02 — System Architecture](02_System_Architecture.md) | End-to-end lifecycle, orchestration, deployment views, DE/DS ↔ Web Team boundary |
| [03 — Data Engineering](03_Data_Engineering.md) | Repo structure, naming, config, logging, error handling, idempotency |
| [04 — Data Modeling](04_Data_Modeling.md) | Star schema, fact/dimension design, SCDs, keys, 6-semester grain |
| [05 — Medallion Architecture](05_Medallion_Architecture.md) | Bronze/Silver/Gold detail |
| [06 — Data Warehouse](06_Data_Warehouse.md) | PostgreSQL warehouse design, DDL, access control, `web_service_reader` role |
| [07 — Technology Stack](07_Technology_Stack.md) | Full tool comparisons + final stack (DE/DS scope only) |
| [08 — Faker Data Generator](08_Faker_Data_Generator.md) | Synthetic data design, business rules, full year-level coverage |
| [09 — Data Science](09_Data_Science.md) | Institutional Success Rate model |
| [10 — Forecasting](10_Forecasting.md) | Feature engineering, model comparison, evaluation |
| [11 — Data Consumption Contract](11_Data_Consumption_Contract.md) | The published interface for the Web Team (formerly "Dashboard Design") |
| [12 — Implementation Roadmap](12_Implementation_Roadmap.md) | Day-by-day 30-day build plan |
| [13 — Best Practices](13_Best_Practices.md) | Testing, code review, governance practices |
| [14 — Future Improvements](14_Future_Improvements.md) | Scoped-out ideas + trigger conditions, including the academic-calendar and ownership migration notes |
| [15 — Tooling Responsibility Matrix](15_Tooling_Responsibility_Matrix.md) | Team ownership boundary + internal tool responsibility split |
| [18 — Internal Architecture Flow](18_Internal_Architecture_Flow.md) | Source→Ingestion→Bronze→Silver→Gold→Warehouse→Features→ML→Forecast, mapped to Dagster assets and code (web/Supabase intentionally excluded) |
| [19 — Data Contracts](19_Data_Contracts.md) | Bronze/Silver/canonical schema, data types, academic-year/semester/year-level rules, cross-entity business rules, validation enforcement |
| [20 — ML Assumptions](20_ML_Assumptions.md) | Forecast target and grain, training window and walk-forward folds, evaluation metrics, promotion and retraining rules |

## Quick Start (once implemented per the roadmap)

```bash
git clone <repo>
cd university-analytics-platform
cp .env.example .env
docker compose up -d
make bootstrap   # runs migrations, seeds config, generates Faker data
```

## Handing Off to the Web Team

Once `docker compose up` is running, the Web Team needs exactly one thing from this repo: **`web_service_reader` credentials**, scoped to `SELECT` on `gold`/`marts` only (see `docs/06_Data_Warehouse.md` §5 and `docs/11_Data_Consumption_Contract.md`). Nothing else in this repo needs to be shared, run, or understood by them.

## Tech Stack at a Glance (DE/DS Scope)

Python · DuckDB · PostgreSQL · MinIO · Dagster · dbt Core · Great Expectations · Prophet · Docker · GitHub

*(Presentation/dashboard tooling is the Web Team's own choice and is not part of this stack — see `docs/07_Technology_Stack.md` §5.)*

## Core Design Philosophy

1. **Separation of concerns** at every layer — ingestion, cleaning, business logic, and presentation never mix.
2. **Config over code** — colleges, programs, the academic calendar, and business rules are data, not hardcoded logic.
3. **One source of truth per metric** — every KPI is computed exactly once, in Gold, and never recomputed by any consumer, internal or external.
4. **Idempotency everywhere** — every job can be safely re-run.
5. **Explicit service boundaries** — the Web Team is a read-only consumer of a tested, documented contract, never a co-owner of this repo's business logic.
6. **Match tool complexity to actual data shape** — no Spark/Kafka/cloud-warehouse cargo-culting at a data volume that doesn't need it, with the scale-trigger for each documented in `docs/14_Future_Improvements.md`.