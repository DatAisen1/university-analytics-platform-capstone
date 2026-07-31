# University Academic Analytics and Forecasting System
### NEUST Sumacab Campus — Institutional Success Rate Platform

A production-inspired data engineering + analytics + forecasting platform, designed as a one-month university capstone but architected the way a real enterprise data platform would be — layered, governed, tested, and reproducible.

## What This Is

Not a dashboard. A **data engineering platform**: batch ingestion → Bronze/Silver/Gold medallion pipeline → dimensional warehouse → analytics engineering (dbt) → forecasting (Prophet) → dashboards, built entirely on free, open-source, self-hosted tools (`docker compose up` and nothing else required).

## Documentation Index

| Doc | Contents |
|---|---|
| [01 — Project Overview](01_Project_Overview.md) | Problem framing, objectives, scope boundaries, success criteria |
| [02 — System Architecture](02_System_Architecture.md) | End-to-end lifecycle, orchestration, deployment views |
| [03 — Data Engineering](03_Data_Engineering.md) | Repo structure, naming, config, logging, error handling, idempotency |
| [04 — Data Modeling](04_Data_Modeling.md) | Star schema, fact/dimension design, SCDs, keys |
| [05 — Medallion Architecture](05_Medallion_Architecture.md) | Bronze/Silver/Gold detail |
| [06 — Data Warehouse](06_Data_Warehouse.md) | PostgreSQL warehouse design, DDL, access control |
| [07 — Technology Stack](07_Technology_Stack.md) | Full tool comparisons + final stack |
| [08 — Faker Data Generator](08_Faker_Data_Generator.md) | Synthetic data design, business rules |
| [09 — Data Science](09_Data_Science.md) | Institutional Success Rate model |
| [10 — Forecasting](10_Forecasting.md) | Feature engineering, model comparison, evaluation |
| [11 — Dashboard](11_Dashboard.md) | Full dashboard suite design |
| [12 — Implementation Roadmap](12_Implementation_Roadmap.md) | Day-by-day 30-day build plan |
| [13 — Best Practices](13_Best_Practices.md) | Testing, code review, governance practices |
| [14 — Future Improvements](14_Future_Improvements.md) | Scoped-out ideas + trigger conditions |

## Quick Start (once implemented per the roadmap)

```bash
git clone <repo>
cd university-analytics-platform
cp .env.example .env
docker compose up -d
make bootstrap   # runs migrations, seeds config, generates Faker data
```

## Tech Stack at a Glance

Python · DuckDB · PostgreSQL · MinIO · Dagster · dbt Core · Great Expectations · Apache Superset · Streamlit · Prophet · Docker · GitHub

## Core Design Philosophy

1. **Separation of concerns** at every layer — ingestion, cleaning, business logic, and presentation never mix.
2. **Config over code** — colleges, programs, and business rules are data, not hardcoded logic.
3. **One source of truth per metric** — every KPI is computed exactly once, in Gold.
4. **Idempotency everywhere** — every job can be safely re-run.
5. **Match tool complexity to actual data shape** — no Spark/Kafka/cloud-warehouse cargo-culting at a data volume that doesn't need it, with the scale-trigger for each documented in `14_Future_Improvements.md`.
