# 16 — Module Responsibility Audit (Task 56)

This project organizes code by **medallion layer** (Bronze/Silver/Gold), not by
generic function name. This doc maps that structure onto the generic
`api/ core/ ingestion/ validation/ transformation/ warehouse/ ml/ storage/`
template so the mapping is explicit and auditable, without physically
moving files (which would break dbt profile paths, Dagster asset imports,
and the existing 291-test suite for zero architectural benefit).

| Generic category | Actual location(s)                                      | Notes |
|---|---|---|
| `ingestion/`      | `pipelines/ingestion/`                                   | Already isolated 1:1. |
| `validation/`     | `pipelines/silver/validate_and_dedupe.py`, `pipelines/silver/progression_validation.py`, `data_generator/validation/` | Split across Silver (real pipeline data) and the synthetic generator (Faker output QA) — different concerns, correctly separate. |
| `transformation/` | `pipelines/silver/clean_entities.py`, `pipelines/silver/cleaning_rules.py`, `pipelines/silver/business_rules.py`, `pipelines/gold/build_dimensions.py`, `pipelines/gold/build_facts.py`, `pipelines/gold/build_kpi.py` | Deliberately split by layer: Silver = correctness rules, Gold = business/metric rules. Flattening into one `transformation/` would erase that distinction. |
| `warehouse/`      | `warehouse/ddl/` (schema) + `pipelines/*/load_*_to_postgres.py` (write path) + `pipelines/common/postgres.py` (connections/roles) | Schema-as-code and write-path are intentionally separate files. |
| `ml/`             | `models/forecasting/`                                    | Contains features (`build_ml_features.py` is actually in `pipelines/gold/` since it's Gold-grain, feeding `models/forecasting/`), training (`train_prophet.py`), evaluation (`metrics.py`), versioning (`model_registry.py`), inference (`deploy_forecast.py`). |
| `storage/`        | `pipelines/common/storage.py`                            | Single file today (`ObjectStorage` ABC + `LocalFileStorage`/`S3Storage`). Single-file is fine at current scope; split into a package only if a 3rd backend is added. |
| `core/`           | `pipelines/common/`                                      | Shared cross-cutting concerns: `config.py`, `errors.py`, `metadata.py`, `schemas.py`, `academic_periods.py`. This *is* your `core/`, named for what it contains rather than a generic label. |
| `api/`            | **Does not exist — by design.** See `docs/17_Consumption_Boundary_MinIO_Supabase.md`. | The Consumption Boundary is enforced by Supabase's auto-generated API + Postgres role grants, not custom route code owned by this repo. |
| `tests/`, `docs/`, `configs/` | `tests/{unit,integration,data_quality}`, `docs/`, `configs/` | Already 1:1 with the template. |

**Verdict:** No physical restructuring recommended. Every generic category
has a real, single-responsibility home; the naming difference is medallion-
layer-first vs. function-first, and layer-first is the correct choice for
a pipeline whose entire value proposition (per `docs/02_System_Architecture.md`)
is that Bronze/Silver/Gold are enforced boundaries, not folders of convenience.