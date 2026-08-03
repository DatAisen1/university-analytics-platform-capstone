# University Academic Analytics and Forecasting System

**NEUST Sumacab Campus — Institutional Success Rate Platform**

A production-inspired Data Engineering + Analytics + Forecasting platform built as a university capstone. It is architected the way a real enterprise data platform would be: layered (Bronze/Silver/Gold), governed, tested, and fully reproducible on free/open-source tools.

> This is a **data engineering project first**. The dashboard is the last mile, not the point.

## Problem

University administrators currently have no unified, historically-consistent way to answer multi-dimensional questions like *"is our institutional success rate improving, and where should we intervene?"* This platform turns semester-cadence registrar-style extracts into a governed warehouse, a defined Success Rate KPI, and short-horizon forecasts.

Full problem framing: [`docs/01_Project_Overview.md`](docs/01_Project_Overview.md)

## Architecture (at a glance)

```
Source → Ingestion (batch) → Bronze → Silver → Gold → Warehouse → Analytics (dbt) → ML (Prophet) → Dashboard → Decision Support
```

Full diagrams and rationale: [`docs/02_System_Architecture.md`](docs/02_System_Architecture.md)

## Documentation

All design decisions — with the *why*, not just the *what* — live in [`docs/`](docs/README.md). Start there for the complete blueprint: data modeling, medallion architecture, warehouse design, tech stack justification, the Faker generator, the Success Rate model, forecasting model selection, dashboard design, the 30-day roadmap, and best practices.

## Repository Structure

```
configs/        # colleges, programs, business rules — config, not code
data_generator/ # synthetic data generator (renamed from faker/ to avoid colliding with the faker PyPI library)
pipelines/      # ingestion, bronze, silver, gold transforms
warehouse/      # DDL and migrations for the PostgreSQL warehouse
dbt/            # analytics engineering: staging + marts
analytics/      # ad-hoc SQL / exploratory outputs
models/         # ML feature engineering + training code
forecasting/    # forecast job entrypoints and artifacts
dashboard/      # Superset/Streamlit assets
notebooks/      # exploration only — never production logic
scripts/        # one-off operational scripts (backfill, seed, etc.)
tests/          # unit, integration, data_quality
docker/         # docker-compose + Dockerfiles
logs/           # structured pipeline logs
docs/           # full project blueprint documentation
```

## Status

🚧 **Week 3 in progress (Day 20 of 30)** — real PostgreSQL warehouse with RBAC (Day 15), dbt staging + marts (Days 16–17), Dagster orchestration (Day 18), `gold.ml_forecast_features` (Day 19), and now Prophet forecasting with walk-forward validation (Day 20) — trained on 16 series, an honest 50%-beats-baseline result traced to its exact cause (100% on `enrollment_count`, 0% on `graduation_count`, tied directly to the Week 1 cohort-truncation limitation). 291 total pytest tests. See [`docs/12_Implementation_Roadmap.md`](docs/12_Implementation_Roadmap.md) for the day-by-day plan.

**A note on repository history:** the sandbox environment reset mid-Day-20, wiping `.git` and all installed tooling. Days 1–19 were fully reconstructed and re-verified (row counts, dbt tests, and the pytest suite all match their pre-reset state exactly) — see the first commit's message for the full account. Per-day commit granularity resumes from Day 20 onward.

**A note on this environment:** MinIO and Postgres run in Docker (Day 2), and this development environment has no Docker daemon. Every stage that would touch them is built against a real interface with a local-filesystem/DuckDB implementation used here (see `docs/03_Data_Engineering.md` §13), so the same code works once Postgres comes online in Week 3 — only the storage target changes.

## Local Setup

### 1. Python environment

```bash
git clone <repo-url>
cd university-analytics-platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
# then edit .env and set real local passwords for POSTGRES_PASSWORD / MINIO_ROOT_PASSWORD
```

`docker-compose.yml` now refuses to start Postgres or MinIO with a blank
credential (`POSTGRES_USER`/`PASSWORD`/`DB`, `MINIO_ROOT_USER`/`PASSWORD`) --
if `.env` is missing or incomplete, `docker compose up` fails immediately
with an explicit "X is not set -- run cp .env.example .env..." error instead
of silently starting with empty values. It also finds the repo-root `.env`
correctly whether you run Compose from the repo root or from `docker/`.

### 3. Start Postgres + MinIO

Recommended (works from any directory, always passes the right flags):
```bash
make up
```
Equivalent, if you'd rather not use `make`:
```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

### 4. Verify both services are up and healthy

A single `docker compose ps` is a point-in-time snapshot -- a container can
sit at "starting" for a while before its healthcheck ever reports healthy
(or never does, if something's wrong). For a clean-start verification that
actually polls health status and fails loudly on a timeout:
```bash
make clean-start
# runs: down -v -> up -d -> polls postgres/minio until "healthy" (or times
# out with logs), then checks pg_isready and MinIO's liveness endpoint too
```
Dagster is intentionally not a docker-compose service in this project (it's
run via the `dagster` CLI against the host Python environment) -- `make
clean-start` verifies it instead by validating `orchestration/definitions.py`
loads (`dagster definitions validate`), which is the CLI-orchestrator
equivalent of a healthcheck here.

Or just the snapshot, if you prefer:
```bash
docker compose -f docker/docker-compose.yml ps
# both "postgres" and "minio" should show state "healthy", not just "running"
```

**Verify Postgres accepts connections:**
```bash
docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "SELECT version();"
```

**Verify MinIO actually has data in it (not just that it's running):** a
pipeline script exiting successfully is not proof its output exists in
MinIO. Check through all three channels:
```bash
make verify-minio
# Channel 1 (Python client, boto3): runs automatically, lists real object
#   counts/sizes per bucket -- an empty bucket "exists" too, so this checks
#   contents, not presence.
# Channel 2 (CLI): prints the exact `mc ls ... --recursive` commands to
#   cross-check against.
# Channel 3 (Console): prints the http://localhost:9001 URL and what to
#   click through to confirm the same objects visually.
```
Every MinIO/S3 write in the pipeline itself (`pipelines/common/storage.py`'s
`S3Storage.write_bytes`) now also reads each object back immediately after
writing it and raises before the pipeline can report success if that
read-back fails or the size doesn't match -- so a silently-lost write is
caught at write time, not discovered later by an audit.

### 5. Tear down (when needed)

```bash
make down      # stop containers, keep data
make down-v    # stop containers AND wipe volumes (clean slate)
```
Equivalent, without `make`:
```bash
docker compose -f docker/docker-compose.yml down        # stop containers, keep data
docker compose -f docker/docker-compose.yml down -v      # stop containers AND wipe volumes (clean slate)
```

### 6. Generate the synthetic dataset

Three stages, run in this exact order (each depends on the previous stage's output):

```bash
python -m data_generator.generators.generate_students     # student_master.csv + internal risk profiles
python -m data_generator.generators.generate_progression   # enrollment/graduation/dropout/shifter, per semester
python -m data_generator.generators.apply_noise             # realistic messiness on top (typos, duplicates, late corrections)
```

Output lands in `data_generator/output/` (git-ignored — regenerate anytime; it's deterministic given the seeds in `data_generator/config/*.yaml`).

### 7. Run the Bronze → Silver → Gold pipeline

Run in this order (each stage reads the previous stage's output):

```bash
python -m pipelines.ingestion.ingest_to_bronze   # lands raw data as Parquet, stamped with audit metadata
python -m pipelines.silver.clean_entities         # normalizes text, resolves noisy enrollment_status
python -m pipelines.silver.validate_and_dedupe     # dedupes enrollment, quarantines business-rule violations
python -m pipelines.gold.build_dimensions          # dim_student (SCD2), dim_program, dim_college, dim_semester, ...
python -m pipelines.gold.build_facts               # fact_enrollment, fact_graduation, fact_dropout, fact_shifter, fact_retention
python -m pipelines.gold.build_kpi                 # fact_institution_kpi -- the weighted Success Rate composite
```

Output lands in `warehouse/bronze_store/`, `warehouse/silver_store/`, `warehouse/gold_store/` (all git-ignored — local stand-ins for MinIO's buckets in this Docker-less environment; see the note in Status above). Pipeline run history is queryable in `warehouse/meta.duckdb`'s `pipeline_run_log` table.

### 8. Bootstrap the Postgres warehouse (roles, schemas, grants)

Requires the service role passwords from `.env` (`PIPELINE_WRITER_PASSWORD`, `DBT_ROLE_PASSWORD`, `DASHBOARD_READER_PASSWORD`, `ANALYST_READONLY_PASSWORD`):

```bash
python -m pipelines.common.postgres   # idempotent -- safe to re-run
```

### 9. Load Gold into Postgres, build ML features, and run dbt

```bash
# After every Gold rebuild:
python -m pipelines.gold.load_gold_to_postgres   # materializes Gold Parquet -> real gold.* tables
python -m pipelines.gold.build_ml_features        # gold.ml_forecast_features -- lag/rolling/trend/seasonality features

# dbt (staging views + marts over Gold, in dbt/models/staging/ and dbt/models/marts/):
export DBT_PROFILES_DIR=dbt
dbt deps --project-dir dbt      # installs dbt_utils (composite-key uniqueness tests)
dbt run --project-dir dbt
dbt test --project-dir dbt

# dbt's auto-generated documentation site (lineage graph, column-level docs):
dbt docs generate --project-dir dbt
dbt docs serve --project-dir dbt   # opens the site at http://localhost:8080
```

### 10. Run the full pipeline through Dagster orchestration

Requires the same env vars as above (`PIPELINE_WRITER_PASSWORD`, `DBT_ROLE_PASSWORD`, `POSTGRES_*`):

```bash
# Materialize every asset (Bronze -> Silver -> Gold -> dbt + ML features), in dependency order:
dagster asset materialize --select "*" -f orchestration/definitions.py

# Or launch the Dagster UI to trigger runs and view the lineage graph interactively:
dagster dev -f orchestration/definitions.py
# then open http://localhost:3000
```

### 11. Train and evaluate the Prophet forecasting models

```bash
python -m models.forecasting.train_prophet
```

Writes trained model artifacts to `forecasting/artifacts/{college_id}_{metric}_prophet.pkl` and an evaluation report to `forecasting/artifacts/evaluation_report.{csv,md}` — see `docs/10_Forecasting.md` Section 5.1 for the real results and what they mean.

### 12. Run the test suite

```bash
python -m pytest
```

## Tech Stack

Python · DuckDB · PostgreSQL · MinIO · Dagster · dbt Core · Great Expectations · Apache Superset · Streamlit · Prophet · Docker

## License

MIT — see [`LICENSE`](LICENSE).