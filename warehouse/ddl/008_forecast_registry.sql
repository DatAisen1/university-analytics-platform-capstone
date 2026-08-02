-- warehouse/ddl/008_forecast_registry.sql
--
-- Task 39 (Champion/Candidate/Promote) + Day 21 (Forecast Write-Back).
-- Run AFTER 003_gold_star_schema.sql (needs gold.dim_college) and
-- 007_ml_forecast_features.sql. Picked up automatically by
-- pipelines.common.migrations.apply_migrations() -- no code change
-- needed to register it, per that module's ordered-by-filename design.
--
-- Two tables:
--
-- gold.model_registry -- one row per TRAINED CANDIDATE, win or lose.
-- Every walk-forward-evaluated model is recorded here, not just the
-- ones that get promoted -- an audit trail of "what did we try and
-- why didn't it win" is exactly what a champion/candidate workflow is
-- supposed to preserve. Deliberately NOT modeled as a mutable
-- "current model" pointer that gets overwritten, per Task 39: the
-- history must survive every retrain.
--
-- gold.fact_forecast -- one row per (college, metric, target period,
-- model_version) that a champion actually produced. Only promoted
-- (champion) models ever write here -- a rejected candidate never
-- reaches this table, so a bad model can never silently overwrite a
-- good forecast already in production.
--
-- target_academic_year/target_semester_number/target_period_ordinal
-- are stored as plain columns rather than an academic_period_key FK
-- into gold.dim_academic_period. That dimension is built by
-- pipelines/gold/build_dimensions.py::build_dim_academic_period() from
-- the fixed, closed ACADEMIC_YEARS = [2021, 2022, 2023, 2024] constant
-- (observed history only) -- it does not, and structurally cannot yet,
-- contain a row for a forecasted future period like 2025-1. Adding
-- forecast periods to that dimension is a real, separate change (it
-- would also require extending build_dim_calendar and every downstream
-- consumer that assumes dim_academic_period == observed history) and
-- is out of scope for this task; flagged here rather than silently
-- worked around with a fragile FK that would break the first time this
-- runs. See models/forecasting/deploy_forecast.py module docstring for
-- the same note next to the code that relies on it.

CREATE TABLE IF NOT EXISTS gold.model_registry (
    model_registry_key   BIGSERIAL PRIMARY KEY,
    college_key           SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    metric                 VARCHAR(32) NOT NULL CHECK (metric IN ('enrollment_count', 'graduation_count')),
    model_version           VARCHAR(64) NOT NULL,
    trained_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Walk-forward evaluation metrics (docs/10_Forecasting.md Section 5),
    -- computed by models.forecasting.train_prophet.evaluate_all_series
    -- and carried through unchanged -- this table never recomputes them.
    mae                       NUMERIC(14, 4) NOT NULL,
    rmse                      NUMERIC(14, 4) NOT NULL,
    mape                      NUMERIC(14, 4),              -- NULL when undefined (all-zero actuals in every fold; see models/forecasting/metrics.py::mape)
    r2                        NUMERIC(14, 6) NOT NULL,
    best_baseline_mae         NUMERIC(14, 4) NOT NULL,      -- min(naive_mae, historical_avg_mae) this candidate was judged against
    beats_baseline            BOOLEAN NOT NULL,

    is_champion               BOOLEAN NOT NULL DEFAULT FALSE,
    promoted_at                TIMESTAMPTZ,
    rejected_reason            VARCHAR(256),                 -- NULL iff is_champion; otherwise why promotion was refused
    artifact_path              VARCHAR(256) NOT NULL,

    UNIQUE (college_key, metric, model_version)
);
CREATE INDEX IF NOT EXISTS ix_model_registry_lookup
    ON gold.model_registry (college_key, metric, trained_at DESC);

-- At most one champion per (college, metric) at any time -- this is the
-- database-enforced version of Task 39's "promote only when criteria
-- are satisfied" rule: even a bug in application code cannot leave two
-- rows simultaneously marked champion for the same series.
CREATE UNIQUE INDEX IF NOT EXISTS ux_model_registry_one_champion
    ON gold.model_registry (college_key, metric)
    WHERE is_champion;

CREATE TABLE IF NOT EXISTS gold.fact_forecast (
    fact_forecast_key         BIGSERIAL PRIMARY KEY,
    college_key                 SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    metric                       VARCHAR(32) NOT NULL CHECK (metric IN ('enrollment_count', 'graduation_count')),

    target_academic_year         SMALLINT NOT NULL,
    target_semester_number        SMALLINT NOT NULL CHECK (target_semester_number IN (1, 2)),
    target_period_ordinal          SMALLINT NOT NULL,

    model_registry_key             BIGINT NOT NULL REFERENCES gold.model_registry (model_registry_key),
    model_version                   VARCHAR(64) NOT NULL,   -- denormalized copy of model_registry.model_version so this table is queryable/joinable to dim_college alone, per docs/10_Forecasting.md Section 6 ("tagged with model_version, so historical forecasts remain queryable")

    yhat                             NUMERIC(12, 2) NOT NULL CHECK (yhat >= 0),   -- Day 21 validation checklist: "forecast values are plausible (not negative enrollment, etc.)"
    yhat_lower                       NUMERIC(12, 2) NOT NULL,   -- Prophet's 80% CI lower bound, clipped to >= 0 by the writer (see deploy_forecast.py) rather than a DB CHECK, since Prophet's raw output can legitimately go negative for a near-zero series and the clip is a deliberate presentation decision, not a data-integrity rule
    yhat_upper                       NUMERIC(12, 2) NOT NULL,

    generated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (college_key, metric, target_period_ordinal, model_version)
);
CREATE INDEX IF NOT EXISTS ix_fact_forecast_lookup
    ON gold.fact_forecast (college_key, metric, target_period_ordinal);