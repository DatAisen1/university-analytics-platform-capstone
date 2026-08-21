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
| [12 — Implementation Roadmap](12_Implementation_Roadmap.md) | Day-by-day 30-day build log (historical — records how the platform was actually built, not a forward-looking plan) |
| [13 — Best Practices](13_Best_Practices.md) | Testing, code review, governance practices |
| [14 — Future Improvements](14_Future_Improvements.md) | Scoped-out ideas + trigger conditions, including the academic-calendar and ownership migration notes |
| [15 — Tooling Responsibility Matrix](15_Tooling_Responsibility_Matrix.md) | Team ownership boundary + internal tool responsibility split |
| [16 — Module Responsibility Audit](16_Module_Responsibility_Audit.md) | Maps the medallion-layer folder structure onto a generic api/core/ingestion/... template, with rationale for why no physical restructuring is needed |
| [17 — Consumption Boundary: MinIO + Supabase](17_Consumption_Boundary_MinIO_Supabase.md) | Why there's no custom API service — MinIO (internal) vs. Supabase (Web Team's access point) |
| [18 — Internal Architecture Flow](18_Internal_Architecture_Flow.md) | Source→Bronze→Silver→Gold→Warehouse→dbt→Features→ML→Forecast, mapped to Dagster assets and code (web/Supabase intentionally excluded) |
| [19 — Data Contracts](19_Data_Contracts.md) | Bronze/Silver/canonical schema, data types, academic-year/semester/year-level rules, cross-entity business rules, validation enforcement |
| [20 — ML Assumptions](20_ML_Assumptions.md) | Forecast target and grain, training window and walk-forward folds, evaluation metrics, promotion and retraining rules |

## Prerequisites

| Requirement | Version / detail |
|---|---|
| Python | 3.11 |
| Docker | Any recent version with Compose v2 built in (`docker compose`, not the standalone `docker-compose` v1 binary) |
| Ports free on your machine | `5432` (Postgres), `9000` + `9001` (MinIO API + console) — override via `POSTGRES_PORT` / `MINIO_API_PORT` / `MINIO_CONSOLE_PORT` in `.env` if any are already taken |
| Environment variables | Everything in `.env.example` — see below |

**Environment variables**, copied from `.env.example` and filled in (`cp .env.example .env`):
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_HOST` / `POSTGRES_PORT` — the warehouse itself.
- `PIPELINE_WRITER_PASSWORD`, `DBT_ROLE_PASSWORD`, `DASHBOARD_READER_PASSWORD`, `ANALYST_READONLY_PASSWORD` — one password per least-privilege service role (see `docs/06_Data_Warehouse.md` §5). These are **not optional**: `alembic upgrade head` creates and grants these roles as part of migration `0002_grants`, and fails with a clear "Missing environment variable(s)" error if any are unset.
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `MINIO_*_PORT` / `MINIO_*_BUCKET` — only required if you're running `STORAGE_BACKEND=minio` (see below).
- `STORAGE_BACKEND` — `local` (default; writes Parquet to `warehouse/{bronze,silver,gold}_store/` on disk, no Docker required for this part) or `minio` (writes through real MinIO — requires `docker compose up` first).

## Quick Start — One Canonical Path

This is the actual, verified sequence — the same one this repo's own CI pipeline (`.github/workflows/ci.yml`) runs end-to-end. There is deliberately only **one** documented path; `scripts/run_pipeline_with_minio.py` is a separate, explicitly-opt-in tool for validating the MinIO storage backend specifically (see its own module docstring) — not an alternative Quick Start.

```bash
# 1. Clone and configure
git clone <repo>
cd university-analytics-platform
cp .env.example .env
# edit .env: fill in every value listed under "Environment variables" above

# 2. Bring up stateful services (Postgres + MinIO)
make up
# equivalent to: docker compose -f docker/docker-compose.yml --env-file .env up -d

# 3. Apply migrations — creates schemas, service roles, and grants
alembic upgrade head

# 4. Generate the synthetic source dataset
python -m data_generator.generators.generate_all

# 5. Install dbt's external packages (dbt_utils, used by the marts layer)
dbt deps --project-dir dbt --profiles-dir dbt

# 6. Run the full pipeline: bronze -> silver -> validation -> gold ->
#    warehouse -> dbt run/test -> features -> training -> evaluation -> forecast
dagster job execute -f orchestration/definitions.py -j full_pipeline_job
```

Steps 3–6 are exactly what `.github/workflows/ci.yml`'s `integration-and-dbt-tests` job runs against a fresh Postgres on every push — so "does this still work from a clean clone" is checked automatically, not just asserted here.

**To explore interactively instead of running the full batch job:** `dagster dev -f orchestration/definitions.py` starts the Dagster UI, where you can materialize assets one at a time and inspect each stage's output.

## Handing Off to the Web Team

Once the sequence above has run at least once, the Web Team needs exactly one thing from this repo: **`dashboard_reader` credentials** (user `dashboard_reader`, password = your `DASHBOARD_READER_PASSWORD`), scoped to `SELECT` on `gold`/`marts` only — see `docs/06_Data_Warehouse.md` §5 and `docs/11_Data_Consumption_Contract.md`. Nothing else in this repo needs to be shared, run, or understood by them.

> **Note on role naming:** several docs (`docs/06_Data_Warehouse.md` and others) still refer to this role as `web_service_reader`. That name was never implemented — the actual role created by `migrations/versions/0002_grants.py` is `dashboard_reader`, with identical scope. This README reflects the real role name; the rest of the doc set is a known, tracked follow-up (see `docs/14_Future_Improvements.md`).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `SettingsError: Missing required environment variable(s) for ...` | A required var in `.env` is unset, or `.env` doesn't exist yet | `cp .env.example .env`, fill in every value under "Environment variables" above. `.env` is read directly by Python (`pipelines/common/settings.py`) — exporting the var in your shell instead also works, and takes precedence over `.env`. |
| `alembic upgrade head` fails with `role "pipeline_writer" does not exist` | You're on an alembic version older than migration `0002_grants`, or ran a raw `psql`/manual step that bypassed it | Just re-run `alembic upgrade head` from a clean state — migration `0002` creates all four service roles itself, idempotently, as its first step. No separate bootstrap script is needed. |
| `psycopg2.OperationalError: password authentication failed` | `.env`'s `POSTGRES_PASSWORD` doesn't match what the running Postgres container was actually initialized with | Postgres only applies `POSTGRES_PASSWORD` on first container creation. If you changed `.env` after the first `make up`, the old password is baked into the Docker volume. Run `make down-v` (drops the volume) then `make up` again to reinitialize — this destroys all data in that Postgres container. |
| Alembic says `head` but tables/constraints are missing (`relation "gold.fact_institution_kpi" does not exist`) | `alembic_version` is ahead of what's actually in the database — usually from an interrupted migration run, or manually editing `alembic_version` (never do this — see the engineering guardrails this project follows) | Don't hand-patch `alembic_version`. Roll back to a known-good revision (`alembic downgrade <rev>`) and re-apply, or start from a clean database (`make down-v && make up`) and run `alembic upgrade head` fresh. |
| MinIO container shows `unhealthy` in `docker compose ps` even though it's serving traffic on 9000 | The healthcheck was using `curl`, which MinIO's image stopped bundling as of the `RELEASE.2023-11-01+` line | Already fixed in `docker/docker-compose.yml` — the healthcheck uses `mc ready local` instead. If you still see this, check you're on the pinned image tag (`RELEASE.2024-05-10T01-41-38Z`) and haven't overridden it. |
| `dagster job execute -f orchestration/definitions.py ...` fails immediately with an import/definition error | A missing dependency, or an env var `orchestration/assets.py`'s imports need isn't set (Dagster loads the whole module graph before running anything) | Run `python -c "from orchestration.definitions import defs"` in isolation first — it'll surface the real import error without Dagster's own error wrapping. Confirm `pip install -r requirements.txt` completed cleanly. |
| `dbt run`/`dbt test` fails to connect, or `dbt deps` fails to compile the profile | `DBT_ROLE_PASSWORD` isn't set, or `DBT_PROFILES_DIR` isn't pointing at this repo's `dbt/profiles.yml` | `dbt/profiles.yml` reads `DBT_ROLE_PASSWORD` via `env_var()` with no default — it must be set even for `dbt deps`, which still parses the profile. Always pass `--profiles-dir dbt` (or `export DBT_PROFILES_DIR=dbt`) so dbt doesn't fall back to `~/.dbt/profiles.yml`. |
| `dbt` tests are skipped, not run | `tests/unit/test_dbt_marts.py` / `test_dbt_staging.py` self-skip if Postgres or the `dbt` CLI isn't reachable, **and** `test_dbt_marts.py` additionally requires the marts to already exist (`dbt run` must have succeeded first) | Run the full Quick Start sequence above (steps 3–6) before `pytest` — the marts are built in step 6, not by the test suite itself. |
| Prophet fails to import, or hangs on first use | Prophet's default backend (`cmdstanpy`) compiles a C++/Stan model the first time it's used in a fresh environment — this can take a few minutes and needs a working C++ toolchain, which isn't always present out of the box (especially in minimal containers) | This is expected on a genuinely first run — let it finish once; subsequent runs reuse the compiled model. If it fails outright, install build tools for your OS (e.g. `build-essential` on Debian/Ubuntu) and retry. This project's own `prophet_debug.log` also shows a *separate*, non-fatal issue — `prophet.plot` failing to import `plotly` — which only affects interactive plotting, not training/forecasting itself. |

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