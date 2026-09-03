# 15 — Tooling Responsibility Matrix

One authoritative answer to "why do we have Pandas, DuckDB, PostgreSQL,
and dbt, and who does what?" Every other doc/docstring in this repo that
touches this question should point here instead of re-explaining it.

## Responsibility split

| Tool | Responsibility | Lifetime | Where |
|---|---|---|---|
| **Pandas** | Row-level cleaning, type coercion, SCD2 bookkeeping, ingestion I/O | Per pipeline run | `pipelines/silver/*`, `pipelines/gold/build_dimensions.py` |
| **DuckDB (`:memory:`)** | SQL execution engine for set-based logic (dedup, joins, window functions) that's awkward in pure Pandas | Per function call, discarded after | `clean_entities.py`, `validate_and_dedupe.py`, `build_facts.py`, `build_kpi.py` |
| **DuckDB (`warehouse/meta.duckdb`)** | `pipeline_run_log`: idempotency + audit log for *pipeline runs* (stage, entity, partition, row counts, status) | Persistent, local file, dependency-free | `pipelines/common/metadata.py` |
| **PostgreSQL `meta` schema** | `schema_migrations`: tracks which **DDL files** have been applied to the warehouse — a different concern from pipeline-run tracking | Persistent, server-based | `warehouse/ddl/000_schema_migrations.sql`, `pipelines/common/migrations.py` |
| **PostgreSQL `bronze`/`silver`/`gold`** | The warehouse of record — durable, queryable, RBAC-governed, multi-consumer | Persistent, server-based | `warehouse/ddl/00{1,3,4}_*.sql` |
| **dbt** | SQL modeling/testing **on top of** Postgres `gold` → materializes into `marts`. Never re-derives Gold's business logic (KPI math, SCD2, dedup) — those stay upstream in Python/DuckDB, dbt only joins/labels/aggregates for presentation | Persistent (views/tables in `marts`) | `dbt/models/staging` (1:1 views on `gold.*`), `dbt/models/marts` (joins + labels) |

## Why two "metadata" stores instead of one

`meta.duckdb`'s `pipeline_run_log` and Postgres's `meta.schema_migrations`
answer different questions and are **not duplicates**:

- `pipeline_run_log` — "did *this data load* for (stage, entity,
  partition) already succeed?" Needed by ingestion/Silver/Gold code
  *before* Postgres is even guaranteed to be reachable (e.g. local dev,
  CI, first-time bootstrap) — hence a dependency-free embedded file.
- `schema_migrations` — "which DDL files have been applied to *this*
  Postgres database?" Inherently a Postgres-only question; it doesn't
  exist until Postgres does.

Consolidating both into Postgres would make every pipeline stage
(including the ones that build Bronze/Silver/Gold Parquet, which have no
Postgres dependency today) require a live Postgres connection just to
check idempotency — a real regression, not a simplification. The split
is deliberate, not incidental duplication.

## Anti-pattern this matrix prevents

Do **not** add a fourth place that recomputes something already computed
upstream — e.g. a dbt mart that recalculates `institutional_success_index`
instead of selecting it from `gold.fact_institution_kpi`. If you're about to write
business logic in SQL that already exists in a `pipelines/gold/build_*.py`
module, stop and either (a) select from the existing Gold table, or (b)
move the logic upstream and delete the duplicate.