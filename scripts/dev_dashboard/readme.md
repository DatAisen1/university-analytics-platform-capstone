# Dev Dashboard (personal diagnostic tool -- not the platform deliverable)

## What this is

A single-file Streamlit app you run locally to eyeball this repo's own
outputs while you develop: KPI, trend, forecast, and Prophet model
registry. It exists because `SELECT * FROM gold.model_registry` by hand
every time you retrain gets old.

## What this is *not*

This repo's own docs (`README.md` §"Ownership boundary",
`docs/11_Data_Consumption_Contract.md`, `docs/12_Implementation_Roadmap.md`)
are explicit and repeated: **dashboards are the Web Team's job, not this
repo's.** An earlier version of this project *did* build a Streamlit
dashboard directly into the DE/DS repo and that decision was later
reverted on purpose (`docs/12_Implementation_Roadmap.md` Week 4 note).

This tool does not reopen that decision. It's kept deliberately outside
the platform's real surface area:

| | Platform (graded architecture) | This tool |
|---|---|---|
| Location | `dbt/models/marts/`, `gold/*` | `scripts/dev_dashboard/` |
| Dependency | `requirements.txt` | `requirements-dashboard.txt`, additive (`pip install -r requirements.txt -r requirements-dashboard.txt`) |
| Wired into `docker-compose.yml` / Dagster? | Yes | No |
| Computes any metric? | Yes (that's its job) | No -- every number is `SELECT`ed verbatim from a mart or Gold fact |
| DB role | `pipeline_writer` / `dbt_role` (write) | `dashboard_reader` (read-only, gold+marts only -- same role the real Web Team dashboard would use) |

If a professor or a reviewer asks "why is there a dashboard in the DE/DS
repo," the honest answer is: it's a personal dev script that reads the
same published, read-only contract the Web Team would consume, proving
that contract actually works end-to-end -- it isn't the deliverable
described in `docs/11_Data_Consumption_Contract.md`.

## Running it

```bash
# once the stack is up and the pipeline has produced at least some Gold data:
docker compose up -d          # from repo root, or: make up

# same venv as the rest of the pipeline -- streamlit>=1.55.0's protobuf
# constraint (>=3.20,<8) overlaps dbt-core 1.12.0's (>=6.0,<8.0), verified
# by `pip install --dry-run -r requirements.txt streamlit==1.55.0` (see
# requirements-dashboard.txt for the full check). No separate venv needed.
pip install -r requirements.txt -r requirements-dashboard.txt
streamlit run app.py
```

Needs `DASHBOARD_READER_PASSWORD` set in `.env` (already in
`.env.example`) -- it's the same role `warehouse/ddl/002_grants.sql`
scopes to read-only `SELECT` on `gold`/`marts`, nothing else.

## Known gap while the platform is mid-build

Every tab degrades to an explicit "no rows yet" message rather than an
error or a fabricated empty chart if the relevant mart/table hasn't been
populated yet -- the Forecast and Prophet Models tabs in particular need
`gold.model_registry` / `gold.fact_forecast` to have real rows, which
means the training/evaluation/deploy assets (Dagster) need to have
actually run first.