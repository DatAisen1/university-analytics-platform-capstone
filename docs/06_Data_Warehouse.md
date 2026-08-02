# 06 — Data Warehouse

## 1. Why PostgreSQL

| Requirement | PostgreSQL fit |
|---|---|
| Free / open-source | Yes, fully |
| Handles star-schema OLAP-style queries at this data volume | Yes — tens of thousands of fact rows is trivial for Postgres |
| Works with dbt | First-class dbt-postgres adapter |
| Supports read-only, schema-scoped external access | Native role/grant model — exactly what a Web Team consumption contract needs |
| Local, no cloud dependency | Runs in Docker, no internet required |
| Industry relevance | Extremely widely used; skills transfer directly to Redshift/BigQuery-style warehousing later |

**DuckDB** is used as a *complementary* engine (in the pipeline layer, for fast local Silver/Gold transformations over Parquet — see `07_Technology_Stack.md`), but the **warehouse of record** is PostgreSQL, because it needs to support concurrent reads from the Web Team's service, dbt, and ad-hoc SQL simultaneously — a role DuckDB (single-process, embedded) is not designed for. **BigQuery** is not used because it requires a cloud account/billing setup, which conflicts with the "fully free, fully local, reproducible with `docker compose up`" constraint.

## 2. Warehouse Schema Organization

```sql
CREATE SCHEMA bronze;   -- raw landing (metadata pointers; actual data in MinIO/Parquet)
CREATE SCHEMA silver;   -- cleaned, validated entity tables
CREATE SCHEMA gold;     -- dimensional model: facts + dimensions
CREATE SCHEMA marts;    -- dbt-managed analytics marts — the Web Team's consumption contract
CREATE SCHEMA meta;     -- pipeline_run_log, data quality results, dbt run results
```

Using **Postgres schemas** (not just naming prefixes) to separate Bronze/Silver/Gold/marts inside one database gives clean permission boundaries — e.g., the Web Team's DB user can be granted `SELECT` only on `gold` and `marts`, and explicitly denied on `silver`/`bronze`, enforcing the "external consumers only read Gold/marts" rule at the database level, not just by convention.

## 3. Physical Table Definitions (Representative DDL)

```sql
-- gold.dim_semester
CREATE TABLE gold.dim_semester (
    semester_key      SERIAL PRIMARY KEY,
    semester_id        VARCHAR(12) NOT NULL UNIQUE,   -- e.g. '2022-2023-1'
    semester_number     SMALLINT NOT NULL CHECK (semester_number IN (1,2)),
    academic_year_key   INT NOT NULL REFERENCES gold.dim_academic_year(academic_year_key)
);

-- gold.dim_academic_year
CREATE TABLE gold.dim_academic_year (
    academic_year_key SERIAL PRIMARY KEY,
    year_label         VARCHAR(9) NOT NULL UNIQUE,     -- e.g. '2022-2023', never a bare single year
    start_calendar_year SMALLINT NOT NULL,
    end_calendar_year   SMALLINT NOT NULL
);
-- Exactly 3 rows in scope: 2021-2022, 2022-2023, 2023-2024.

-- gold.dim_student (SCD Type 2)
CREATE TABLE gold.dim_student (
    student_key     SERIAL PRIMARY KEY,
    student_id      VARCHAR(20) NOT NULL,
    gender          VARCHAR(10),
    birth_year      INT,
    home_province   VARCHAR(100),
    admission_type  VARCHAR(20),
    _valid_from_semester_key INT REFERENCES gold.dim_semester(semester_key),
    _valid_to_semester_key   INT REFERENCES gold.dim_semester(semester_key),
    _is_current     BOOLEAN NOT NULL DEFAULT TRUE,
    _batch_id       UUID NOT NULL,
    _loaded_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_dim_student_current
    ON gold.dim_student (student_id) WHERE _is_current;

-- gold.fact_enrollment
CREATE TABLE gold.fact_enrollment (
    fact_id          BIGSERIAL PRIMARY KEY,
    student_key      INT NOT NULL REFERENCES gold.dim_student(student_key),
    program_key      INT NOT NULL REFERENCES gold.dim_program(program_key),
    college_key      INT NOT NULL REFERENCES gold.dim_college(college_key),
    semester_key      INT NOT NULL REFERENCES gold.dim_semester(semester_key),
    academic_year_key INT NOT NULL REFERENCES gold.dim_academic_year(academic_year_key),
    year_level_key    INT NOT NULL REFERENCES gold.dim_year_level(year_level_key),
    enrollment_status VARCHAR(20) NOT NULL,
    units_enrolled    SMALLINT,
    is_new_enrollee   BOOLEAN,
    _batch_id         UUID NOT NULL,
    _loaded_at        TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_enrollment_college_sem ON gold.fact_enrollment (college_key, semester_key);
```

Every fact table follows the same pattern: surrogate FK columns + audit columns + one composite index matching the dominant query shape (`college × semester`), which any downstream consumer — including the Web Team — will use most often.

## 4. Refresh Strategy

- **Dimensions**: `MERGE`/upsert on each Gold run; `dim_student` inserts a new SCD2 row + closes the old one (`_valid_to_semester_key`, `_is_current = false`) only when a tracked attribute (program, status-relevant fields) changes.
- **Facts**: at this data volume, Gold facts are fully rebuilt from Silver on every run rather than incrementally merged (see `04_Data_Modeling.md` §9 for why) — this sidesteps a class of incremental-merge bugs and stays idempotent by construction (same input → same output, every time).
- **`fact_institution_kpi`**: fully recomputed per affected `(college, semester)` on every Gold run — cheap enough at this volume (48 rows total) to just recompute rather than incrementally maintain.

## 5. Access & Security Model

| Role | Access | Notes |
|---|---|---|
| `pipeline_writer` | Full read/write on `bronze`, `silver`, `gold` schemas | Used only by this repo's own pipeline jobs |
| `dbt_role` | Read on `gold`, read/write on `marts` | Used only by this repo's dbt project |
| `web_service_reader` | Read-only on `gold`, `marts` only | **Granted to the Web Team.** No write access anywhere, no read access on `silver`/`bronze`. This is the entire database-level enforcement of the service boundary described in `02_System_Architecture.md` §3.9. |
| `analyst_readonly` | Read-only on `marts` only | For ad-hoc internal analysis, narrower than `web_service_reader` |

This tiered access is a small-scale version of real data-platform governance: **write access is scoped to the layer a role is responsible for**, and any external consumer — the Web Team included — never gets write access at all, and never gets to see the un-conformed `silver`/`bronze` layers where business logic hasn't been applied yet.

## 6. Backup & Recovery

- Nightly `pg_dump --format=custom` to a local volume, retained 14 days on a rolling basis.
- Because Bronze (in MinIO/Parquet) is the true immutable source of record, the warehouse itself is technically **rebuildable from Bronze** — the Postgres backup is a convenience for fast recovery, not the last line of defense.

---
*Next: `07_Technology_Stack.md` — full comparison and final open-source stack.*