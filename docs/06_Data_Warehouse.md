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
-- gold.dim_academic_period
-- Task 23/24 Gold Modeling Fix: replaces the old snowflaked
-- dim_academic_year <- dim_semester pair (see 04_Data_Modeling.md §2/§3
-- for why) with one denormalized row per (academic_year, semester_number).
-- Exactly 6 rows in scope: 2021-2022 through 2023-2024, 2 semesters each.
CREATE TABLE gold.dim_academic_period (
    academic_period_key SMALLINT PRIMARY KEY,
    academic_year        SMALLINT NOT NULL,
    semester_number       SMALLINT NOT NULL CHECK (semester_number IN (1, 2)),
    year_label            VARCHAR(16) NOT NULL,          -- e.g. '2022-2023'
    semester_label         VARCHAR(16) NOT NULL,          -- '1st Semester' / '2nd Semester'
    period_label           VARCHAR(32) NOT NULL,          -- combined, e.g. '2022-2023 · 1st Semester'
    period_ordinal         SMALLINT NOT NULL,              -- 0-based chronological ordinal
    UNIQUE (academic_year, semester_number),
    UNIQUE (period_ordinal)
);

-- gold.dim_gender / gold.dim_year_level
-- Also new in Task 23/24: gender and year_level promoted from raw
-- fact/dimension values into first-class governed dimensions.
CREATE TABLE gold.dim_gender (
    gender_key    SMALLINT PRIMARY KEY,
    gender_code   VARCHAR(16) NOT NULL UNIQUE,
    gender_label  VARCHAR(16) NOT NULL
);

CREATE TABLE gold.dim_year_level (
    year_level_key    SMALLINT PRIMARY KEY,
    year_level        SMALLINT NOT NULL UNIQUE,
    year_level_label  VARCHAR(32) NOT NULL
);

-- gold.dim_student (SCD Type 2)
CREATE TABLE gold.dim_student (
    student_key             INTEGER PRIMARY KEY,
    student_id              VARCHAR(16) NOT NULL,
    gender_key               SMALLINT NOT NULL REFERENCES gold.dim_gender(gender_key),
    birth_year                SMALLINT NOT NULL,
    home_province             VARCHAR(64) NOT NULL,
    admission_type            VARCHAR(16) NOT NULL,
    college_key               SMALLINT NOT NULL REFERENCES gold.dim_college(college_key),
    program_key               INTEGER NOT NULL REFERENCES gold.dim_program(program_key),
    _valid_from_period_key     SMALLINT NOT NULL REFERENCES gold.dim_academic_period(academic_period_key),
    _valid_to_period_key       SMALLINT REFERENCES gold.dim_academic_period(academic_period_key),
    _is_current                BOOLEAN NOT NULL
);
CREATE UNIQUE INDEX ux_dim_student_one_current
    ON gold.dim_student (student_id) WHERE _is_current;

-- gold.fact_enrollment
CREATE TABLE gold.fact_enrollment (
    student_key            INTEGER NOT NULL REFERENCES gold.dim_student(student_key),
    program_key             INTEGER NOT NULL REFERENCES gold.dim_program(program_key),
    college_key             SMALLINT NOT NULL REFERENCES gold.dim_college(college_key),
    academic_period_key      SMALLINT NOT NULL REFERENCES gold.dim_academic_period(academic_period_key),
    enrollment_status         VARCHAR(16) NOT NULL,
    year_level_key             SMALLINT NOT NULL REFERENCES gold.dim_year_level(year_level_key),
    units_enrolled              SMALLINT NOT NULL,
    is_new_enrollee              BOOLEAN NOT NULL
);
CREATE INDEX ix_fact_enrollment_period ON gold.fact_enrollment (college_key, academic_period_key);
```

This is a direct summary of the authoritative DDL in `warehouse/ddl/003_gold_star_schema.sql` — see that file for the remaining fact tables (`fact_graduation`, `fact_dropout`, `fact_shifter`, `fact_retention`, `fact_institution_kpi`), each of which follows the same pattern: surrogate FK columns + one composite index matching the dominant query shape (`college × academic_period`), which any downstream consumer — including the Web Team — will use most often. `dim_college`, `dim_program`, and `dim_calendar` are omitted above for brevity; see `04_Data_Modeling.md` §3 for their full column lists.

## 4. Refresh Strategy

- **Dimensions**: `MERGE`/upsert on each Gold run; `dim_student` inserts a new SCD2 row + closes the old one (`_valid_to_period_key`, `_is_current = false`) only when a tracked attribute (program, status-relevant fields) changes.
- **Facts**: at this data volume, Gold facts are fully rebuilt from Silver on every run rather than incrementally merged (see `04_Data_Modeling.md` §9 for why) — this sidesteps a class of incremental-merge bugs and stays idempotent by construction (same input → same output, every time).
- **`fact_institution_kpi`**: fully recomputed per affected `(college, academic_period)` on every Gold run — cheap enough at this volume (8 colleges × 6 periods = 48 rows total) to just recompute rather than incrementally maintain.

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