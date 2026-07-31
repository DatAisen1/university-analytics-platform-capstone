# 06 — Data Warehouse

## 1. Why PostgreSQL

| Requirement | PostgreSQL fit |
|---|---|
| Free / open-source | Yes, fully |
| Handles star-schema OLAP-style queries at this data volume | Yes — tens of thousands of fact rows is trivial for Postgres |
| Works with dbt | First-class dbt-postgres adapter |
| Works with Superset/Metabase | Native connector |
| Local, no cloud dependency | Runs in Docker, no internet required |
| Industry relevance | Extremely widely used; skills transfer directly to Redshift/BigQuery-style warehousing later |

**DuckDB** is used as a *complementary* engine (in the pipeline layer, for fast local Silver/Gold transformations over Parquet — see `07_Technology_Stack.md`), but the **warehouse of record** is PostgreSQL, because it needs to support concurrent reads from the dashboard, dbt, and ad-hoc SQL simultaneously — a role DuckDB (single-process, embedded) is not designed for. **BigQuery** is not used because it requires a cloud account/billing setup, which conflicts with the "fully free, fully local, reproducible with `docker compose up`" constraint — it would work technically but adds a dependency the project doesn't need.

## 2. Warehouse Schema Organization

```sql
CREATE SCHEMA bronze;   -- raw landing (metadata pointers; actual data in MinIO/Parquet)
CREATE SCHEMA silver;   -- cleaned, validated entity tables
CREATE SCHEMA gold;     -- dimensional model: facts + dimensions
CREATE SCHEMA marts;    -- dbt-managed analytics marts
CREATE SCHEMA meta;     -- pipeline_run_log, data quality results, dbt run results
```

Using **Postgres schemas** (not just naming prefixes) to separate Bronze/Silver/Gold/marts inside one database gives clean permission boundaries — e.g., the dashboard's DB user can be granted `SELECT` only on `gold` and `marts`, and explicitly denied on `silver`/`bronze`, enforcing the "dashboards only read Gold" rule at the database level, not just by convention.

## 3. Physical Table Definitions (Representative DDL)

```sql
-- gold.dim_student (SCD Type 2)
CREATE TABLE gold.dim_student (
    student_key     SERIAL PRIMARY KEY,
    student_id      VARCHAR(20) NOT NULL,
    gender          VARCHAR(10),
    birth_year      INT,
    home_province   VARCHAR(100),
    admission_type  VARCHAR(20),
    _valid_from     DATE NOT NULL,
    _valid_to       DATE,
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
    enrollment_status VARCHAR(20) NOT NULL,
    year_level        SMALLINT,
    units_enrolled    SMALLINT,
    is_new_enrollee   BOOLEAN,
    _batch_id         UUID NOT NULL,
    _loaded_at        TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_enrollment_college_sem ON gold.fact_enrollment (college_key, semester_key);
```

Every fact table follows the same pattern: surrogate FK columns + audit columns + one composite index matching the dominant dashboard query shape (`college × semester`).

## 4. Refresh Strategy

- **Dimensions**: `MERGE`/upsert on each Gold run; `dim_student` inserts a new SCD2 row + closes the old one (`_valid_to`, `_is_current = false`) only when a tracked attribute (program, status-relevant fields) changes.
- **Facts**: append-only inserts per batch, keyed by `(natural_key, semester)` with `ON CONFLICT DO UPDATE` to guarantee idempotent re-runs.
- **`fact_institution_kpi`**: fully recomputed per affected `(college, semester)` on every Gold run — cheap enough at this volume to just recompute rather than incrementally maintain, which avoids an entire class of "aggregate table drifted from source" bugs.

## 5. Access & Security Model

| Role | Access |
|---|---|
| `pipeline_writer` | Full read/write on `bronze`, `silver`, `gold` schemas |
| `dbt_role` | Read on `gold`, read/write on `marts` |
| `dashboard_reader` | Read-only on `gold`, `marts` |
| `analyst_readonly` | Read-only on `marts` only |

This tiered access is a small-scale version of real data-platform governance: the principle that **write access is scoped to the layer a role is responsible for**, and presentation-layer consumers never get write access at all.

## 6. Backup & Recovery

- Nightly `pg_dump --format=custom` to a local volume, retained 14 days on a rolling basis.
- Because Bronze (in MinIO/Parquet) is the true immutable source of record, the warehouse itself is technically **rebuildable from Bronze** — the Postgres backup is a convenience for fast recovery, not the last line of defense.

---
*Next: `07_Technology_Stack.md` — full comparison and final open-source stack.*
