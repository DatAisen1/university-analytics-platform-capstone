University Academic Analytics and Forecasting System

NEUST Sumacab Campus — Institutional Success Rate Platform

A production-inspired Data Engineering + Analytics + Forecasting platform built as a university capstone. The platform uses a Bronze → Silver → Gold pipeline, PostgreSQL as the warehouse, MinIO for object storage, Dagster for orchestration, dbt for analytics engineering, and Prophet for forecasting.

Important: This README is the operational runbook. Follow the commands in the order shown when you want to run the system from a clean environment and evaluate it.

1. Architecture

Synthetic Source Data
        ↓
Ingestion
        ↓
Bronze
        ↓
Silver Cleaning
        ↓
Validation / Deduplication
        ↓
Gold Dimensions + Facts + KPI
        ↓
PostgreSQL Warehouse
        ↓
ML Forecast Features
        ↓
Prophet Training
        ↓
Walk-Forward Evaluation
        ↓
Model Registry / Promotion
        ↓
Next-Semester Forecast

Docker runs the stateful services:

PostgreSQL — warehouse

MinIO — S3-compatible object storage

Dagster, dbt, and the Python pipeline run from the local Python environment.

2. Prerequisites

Install:

Python 3.11+ recommended for the project environment

Docker Desktop

Git

PowerShell on Windows

Python dependencies from requirements.txt

From the repository root:

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

If PowerShell blocks activation:

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

Confirm:

python --version
docker --version
docker compose version

3. FIRST STEP — Create .env

This fixes the POSTGRES_USER is missing error

Do not start Docker before creating .env.

From the repository root:

Copy-Item .env.example .env

Check that it exists:

Test-Path .env

It should return:

True

Open it:

notepad .env

At minimum, make sure these values are present:

POSTGRES_USER=uap_admin
POSTGRES_PASSWORD=change_me_locally
POSTGRES_DB=university_analytics

PIPELINE_WRITER_PASSWORD=change_me_locally_pw
DBT_ROLE_PASSWORD=change_me_locally_dbt
DASHBOARD_READER_PASSWORD=change_me_locally_dash
ANALYST_READONLY_PASSWORD=change_me_locally_analyst

MINIO_ROOT_USER=uap_minio_admin
MINIO_ROOT_PASSWORD=change_me_locally_too

For a local capstone environment, these values can remain local development passwords. Never commit .env to Git.

4. IMPORTANT — Correct Docker Compose Command

Do NOT use this by itself

docker compose -f docker/docker-compose.yml

That command does not start the system, and when Compose cannot find the repository-root .env, interpolation fails with:

required variable POSTGRES_USER is missing a value
POSTGRES_USER is not set

Recommended command from the repository root

Always use:

docker compose --env-file .env -f docker/docker-compose.yml up -d

The order of --env-file and -f is not important, but both must be present for this project.

Or, if make is installed:

make up

The Makefile already expands to the correct Compose command.

5. Verify Docker

Check the containers:

docker compose --env-file .env -f docker/docker-compose.yml ps

You want:

uap_postgres    ... healthy
uap_minio       ... healthy

If they are still starting, wait a few seconds and run the command again.

Check PostgreSQL directly:

docker exec -it uap_postgres pg_isready -U uap_admin -d university_analytics

Expected:

accepting connections

Check MinIO:

Invoke-WebRequest http://localhost:9000/minio/health/live

Expected HTTP status:

200

Useful URLs:

MinIO API: http://localhost:9000

MinIO Console: http://localhost:9001

MinIO login uses:

Username: value of MINIO_ROOT_USER
Password: value of MINIO_ROOT_PASSWORD

6. Recommended Clean Start

When you want to completely rebuild the local Docker state:

docker compose --env-file .env -f docker/docker-compose.yml down -v
docker compose --env-file .env -f docker/docker-compose.yml up -d

Then:

docker compose --env-file .env -f docker/docker-compose.yml ps

down -v deletes the PostgreSQL and MinIO Docker volumes. Use it when you intentionally want a clean rebuild.

7. Generate the Dataset

Run these in this exact order.

Step 1 — Generate students

python -m data_generator.generators.generate_students

Step 2 — Generate progression

python -m data_generator.generators.generate_progression

Step 3 — Apply realistic noise

python -m data_generator.generators.apply_noise

The generated files are placed under:

data_generator/output/

Confirm files exist:

Get-ChildItem data_generator/output

8. Bootstrap PostgreSQL

Before running the warehouse portion of the pipeline, create the required database roles/schemas:

python -m pipelines.common.postgres

This is designed to be idempotent, so it can safely be run again.

Verify PostgreSQL:

docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "\dn"

9. Run the Pipeline Manually

Use this mode when you are debugging a particular stage or learning how each layer works.

By default every command below reads/writes Bronze/Silver/Gold as local
Parquet files under `warehouse/{bronze,silver,gold}_store/`, NOT MinIO --
this is controlled by `STORAGE_BACKEND` in `.env` (default: `local`).
To run the exact same commands against real MinIO instead, set
`STORAGE_BACKEND=minio` in `.env` (with `docker compose up` running and
buckets created via `python -m scripts.create_minio_buckets`) -- no
other change is required, the same entrypoints below and the Dagster
graph both honor it. See `.env.example` for details.

Bronze

python -m pipelines.ingestion.ingest_to_bronze

Silver cleaning

python -m pipelines.silver.clean_entities

Validation + deduplication

python -m pipelines.silver.validate_and_dedupe

Gold dimensions

python -m pipelines.gold.build_dimensions

Gold facts

python -m pipelines.gold.build_facts

Gold KPI

python -m pipelines.gold.build_kpi

Load Gold into PostgreSQL

python -m pipelines.gold.load_gold_to_postgres

Build ML features

python -m pipelines.gold.build_ml_features

10. Run dbt

PowerShell uses $env: instead of Linux/macOS export.

Set the dbt profile directory:

$env:DBT_PROFILES_DIR = "dbt"

Install dbt packages:

dbt deps --project-dir dbt

Run transformations:

dbt run --project-dir dbt

Run dbt tests:

dbt test --project-dir dbt

Generate documentation:

dbt docs generate --project-dir dbt

Serve the documentation:

dbt docs serve --project-dir dbt

Open:

http://localhost:8080

11. Recommended Full Pipeline — Dagster

After:

.env exists

Docker is healthy

Dataset has been generated

PostgreSQL roles/schemas have been bootstrapped

validate Dagster first:

dagster definitions validate -f orchestration/definitions.py

Expected result:

Definitions ... successfully loaded

Then run the entire asset graph:

dagster asset materialize --select "*" -f orchestration/definitions.py

The expected logical flow is:

ingestion
   ↓
bronze
   ↓
silver
   ↓
validation
   ↓
gold
   ↓
warehouse
   ↓
features
   ↓
training
   ↓
evaluation
   ↓
forecast

Important

Dagster does not generate the synthetic dataset.

Run these first:

python -m data_generator.generators.generate_students
python -m data_generator.generators.generate_progression
python -m data_generator.generators.apply_noise

Dagster also does not replace the separate dbt CLI commands in the current implementation. Run dbt separately when you want to build/test the dbt models.

12. Run Dagster UI

For an interactive view of the pipeline:

dagster dev -f orchestration/definitions.py

Open:

http://localhost:3000

From the UI you can inspect:

asset lineage

materialization runs

logs

failures

metadata

pipeline execution

13. Train and Evaluate Prophet

The direct forecasting entry point is:

python -m models.forecasting.train_prophet

This performs:

Walk-forward validation

Prophet evaluation

Naive baseline evaluation

Historical-average baseline evaluation

Model comparison

Final model training on the full history

Artifact generation

The evaluation uses:

MAE

RMSE

MAPE

R²

Prophet vs. naive baseline

Prophet vs. historical-average baseline

Outputs:

forecasting/artifacts/evaluation_report.csv
forecasting/artifacts/evaluation_report.md

Trained models are written under:

forecasting/artifacts/

14. How to Evaluate the System

Evaluation should happen at four levels, not only by checking whether the command exited successfully.

A. Automated software tests

Run:

python -m pytest

For a concise result:

python -m pytest -q

For coverage:

python -m pytest --cov=. --cov-report=term-missing

You want the test suite to finish without failures.

B. Pipeline evaluation

After running the pipeline, inspect the generated layers:

Get-ChildItem warehouse/bronze_store
Get-ChildItem warehouse/silver_store
Get-ChildItem warehouse/gold_store

The important question is:

Did data successfully move through Bronze → Silver → Gold?

You should also verify that Silver has fewer invalid/duplicate records than the raw input and that Gold contains the dimensional/fact/KPI tables expected by the project.

C. PostgreSQL warehouse evaluation

Check schemas:

docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "\dn"

Check Gold tables:

docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "\dt gold.*"

Check KPI rows:

docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "SELECT COUNT(*) FROM gold.fact_institution_kpi;"

Check forecast rows:

docker exec -it uap_postgres psql -U uap_admin -d university_analytics -c "SELECT COUNT(*) FROM gold.fact_forecast;"

D. Forecast-model evaluation

Open:

forecasting/artifacts/evaluation_report.md

Or inspect the CSV:

Import-Csv forecasting/artifacts/evaluation_report.csv | Format-Table

The most important column is:

prophet_beats_best_baseline

The model should not be considered successful merely because Prophet produced a forecast.

A stronger evaluation asks:

Does Prophet beat a simple baseline?

For example:

Prophet MAE       = 100
Naive MAE         = 130
Historical Avg    = 150

Prophet wins in this case.

But:

Prophet MAE       = 150
Naive MAE         = 100
Historical Avg    = 120

Prophet does not beat the baseline. That is a valid evaluation result and should be reported honestly.

15. One Command Sequence to Run the Whole System

Once your environment is already installed, this is the sequence to remember:

# 1. Activate environment
.\.venv\Scripts\Activate.ps1

# 2. Make sure .env exists
Copy-Item .env.example .env -ErrorAction SilentlyContinue

# 3. Start infrastructure
docker compose --env-file .env -f docker/docker-compose.yml up -d

# 4. Verify infrastructure
docker compose --env-file .env -f docker/docker-compose.yml ps

# 5. Generate data
python -m data_generator.generators.generate_students
python -m data_generator.generators.generate_progression
python -m data_generator.generators.apply_noise

# 6. Bootstrap warehouse
python -m pipelines.common.postgres

# 7. Validate Dagster
dagster definitions validate -f orchestration/definitions.py

# 8. Run complete pipeline
dagster asset materialize --select "*" -f orchestration/definitions.py

# 9. Run automated tests
python -m pytest -q

# 10. Inspect forecasting evaluation
Get-Content forecasting/artifacts/evaluation_report.md

If you want to run dbt explicitly, run the dbt section after PostgreSQL has been bootstrapped and the Gold layer has been loaded.

16. Fast Recovery When Something Fails

Error: POSTGRES_USER is not set

Run:

Test-Path .env

If it returns False:

Copy-Item .env.example .env

Then start Compose with:

docker compose --env-file .env -f docker/docker-compose.yml up -d

Error: PostgreSQL container is restarting

Check logs:

docker compose --env-file .env -f docker/docker-compose.yml logs postgres --tail 100

Error: MinIO container is restarting

Check:

docker compose --env-file .env -f docker/docker-compose.yml logs minio --tail 100

Error: Everything is corrupted and you want a clean rebuild

docker compose --env-file .env -f docker/docker-compose.yml down -v
docker compose --env-file .env -f docker/docker-compose.yml up -d
python -m pipelines.common.postgres

Then regenerate data and rerun the pipeline.

17. Important Current-Code Check Before Forecasting

Before interpreting a Prophet failure as a data problem, inspect:

models/forecasting/train_prophet.py

The training DataFrame passed to Prophet must contain:

ds
y

If your local copy contains:

train.rename(columns={metric: "y_col"})

but does not subsequently rename y_col to y, Prophet will reject the training DataFrame.

The same check applies to:

models/forecasting/deploy_forecast.py

This is a code-level prerequisite, not a Docker problem.

18. What Counts as a Successful Capstone Run?

A complete successful run should demonstrate:

Infrastructure

PostgreSQL is healthy

MinIO is healthy

.env is loaded correctly

Data Engineering

Synthetic source data generated

Bronze data created

Silver cleaning completed

Validation/deduplication completed

Gold dimensions/facts/KPI created

Warehouse

PostgreSQL schemas exist

Gold data is loaded

ML feature table is populated

Analytics Engineering

dbt models run

dbt tests pass

Orchestration

Dagster definitions validate

Full asset graph materializes successfully

Machine Learning

Prophet models train

Walk-forward evaluation completes

Baselines are calculated

Evaluation report is produced

Forecast artifacts are generated

Software Quality

python -m pytest -q

passes.

19. Recommended Evaluation Evidence for the Capstone

Keep these outputs as evidence for your documentation/presentation:

1. docker compose ps
2. Dagster asset graph
3. Successful Dagster materialization
4. Bronze/Silver/Gold row counts
5. PostgreSQL Gold table screenshots/query results
6. dbt test results
7. pytest results
8. forecasting/artifacts/evaluation_report.md
9. Prophet vs baseline metrics
10. generated forecast records

The strongest capstone demonstration is:

Raw data
   ↓
Bronze
   ↓
Silver
   ↓
Gold
   ↓
PostgreSQL
   ↓
Dagster orchestration
   ↓
ML features
   ↓
Prophet
   ↓
Walk-forward evaluation
   ↓
Baseline comparison
   ↓
Forecast

This demonstrates the project as a Data Engineering + Analytics + ML system, rather than only a forecasting notebook.

20. Useful Stop Commands

Stop containers but keep data:

docker compose --env-file .env -f docker/docker-compose.yml down

Stop containers and delete Docker volumes:

docker compose --env-file .env -f docker/docker-compose.yml down -v

Follow Docker logs:

docker compose --env-file .env -f docker/docker-compose.yml logs -f

Check running services:

docker compose --env-file .env -f docker/docker-compose.yml ps

Project Documentation

For deeper explanations, see:

docs/

Important documents include:

docs/01_Project_Overview.md
docs/02_System_Architecture.md
docs/03_Data_Engineering.md
docs/06_Data_Warehouse.md
docs/10_Forecasting.md
docs/12_Implementation_Roadmap.md

License

MIT — see LICENSE.