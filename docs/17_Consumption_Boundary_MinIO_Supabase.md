# 17 — Consumption Boundary: MinIO + Supabase (Task 58)

## Decision
No FastAPI/custom API service will be built in this repo. The Consumption
Boundary described in `docs/02_System_Architecture.md` §3.9 is enforced by
two existing pieces of infrastructure instead of new application code:

1. **MinIO** — internal-only. Connects two *of this project's own* stages
   (e.g. Bronze/Silver/Gold object storage today; also usable for handing
   forecast artifacts from `models/forecasting/` to a downstream consumer
   without a live service dependency). MinIO is never exposed to the Web
   Team — it is DE/DS-internal plumbing, consistent with
   `pipelines/common/storage.py`'s existing `ObjectStorage` interface.

2. **Supabase** — the Web Team's access point. Supabase is managed
   PostgreSQL with an auto-generated PostgREST API layered on top of
   whatever schemas/roles you grant it. Practically: **the warehouse's
   Postgres target becomes the Supabase-hosted instance**, and the
   existing `dashboard_reader` / `analyst_readonly` roles (already
   scoped to `gold`/`marts` only, see `warehouse/ddl/002_grants.sql`)
   are exposed through Supabase's auto-API instead of a hand-built one.
   No new route code, no new auth code — the same database-enforced
   boundary this repo already built, just fronted by Supabase's
   generated API instead of custom FastAPI routes.

## What this preserves
- `docs/02_System_Architecture.md`'s Consumption Boundary rule (Web Team
  reads `gold`/`marts` read-only, never writes back) is now *literally*
  the Supabase project's exposed schema list — configured in the
  Supabase dashboard, not code you own.
- `bronze`/`silver`/`meta` remain invisible to `dashboard_reader` at the
  database-permission level (Task per `002_grants.sql`), which Supabase
  cannot override — it only ever proxies through Postgres' own grants.

## What this requires (one real code change)
Supabase enforces TLS on all connections; your current
`pipelines/common/postgres.py` connects without an `sslmode` parameter,
which works against local/Docker Postgres but will be rejected by
Supabase. See the `[MODIFY]` to `pipelines/common/postgres.py` below.