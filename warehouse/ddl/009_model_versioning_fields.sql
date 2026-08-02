-- warehouse/ddl/009_model_versioning_fields.sql
--
-- Tasks 40-42 (Model Versioning). Run AFTER 008_forecast_registry.sql,
-- which created gold.model_registry with model_version/mae/rmse/mape/r2/
-- trained_at/is_champion already in place (Task 39). This migration adds
-- the remaining fields Task 40 requires at minimum -- algorithm, the
-- training data window, and the training record count -- none of which
-- 008 recorded.
--
-- Nullable, not NOT NULL: any gold.model_registry rows written before
-- this migration ran genuinely never recorded this provenance, and
-- backfilling a fabricated value would be worse than an honest NULL.
-- Every row inserted by models/forecasting/deploy_forecast.py AFTER
-- this migration populates all four columns -- enforced in application
-- code (model_registry.TrainingMetadata is a required, non-optional
-- argument to record_candidate()), not just documented here.
--
-- Task 41 ("preserve historical models / which model generated this
-- forecast") needs no new table: gold.model_registry was already
-- INSERT-only for provenance (the only UPDATE any code path issues is
-- is_champion = FALSE on the row being demoted during a promotion --
-- see 008's module docstring). This migration doesn't change that.
-- What it adds is queryable columns to make historical answers useful,
-- not the immutability property itself.

ALTER TABLE gold.model_registry
    ADD COLUMN IF NOT EXISTS algorithm                            VARCHAR(32),
    ADD COLUMN IF NOT EXISTS training_data_start_period_ordinal     SMALLINT,
    ADD COLUMN IF NOT EXISTS training_data_end_period_ordinal        SMALLINT,
    ADD COLUMN IF NOT EXISTS training_record_count                    INTEGER;

COMMENT ON COLUMN gold.model_registry.algorithm IS
    'Forecasting algorithm that produced this candidate (e.g. ''prophet''). Tracked explicitly rather than assumed, so the registry stays correct the day a second algorithm is introduced.';
COMMENT ON COLUMN gold.model_registry.training_data_start_period_ordinal IS
    'Earliest period_ordinal (pipelines/gold/build_dimensions.py::period_ordinal convention) included in this candidate''s training window.';
COMMENT ON COLUMN gold.model_registry.training_data_end_period_ordinal IS
    'Latest period_ordinal included in this candidate''s training window -- Task 42''s retraining gate (models.forecasting.model_registry.should_retrain) compares THIS column against the currently available max period_ordinal, not row counts.';
COMMENT ON COLUMN gold.model_registry.training_record_count IS
    'Row count of the training DataFrame actually fit -- observational/diagnostic only. NOT used as a retraining trigger by itself (Task 42): a changed count within an already-trained period window (e.g. late corrections/backfill) must not, on its own, cause a retrain.';

-- is_champion (added in 008_forecast_registry.sql) already serves Task
-- 40's "is_current" requirement: exactly one TRUE row per
-- (college_key, metric) at all times, enforced by
-- ux_model_registry_one_champion. Not renamed here to avoid breaking
-- the Task 39 code already built on it -- "is_current" and
-- "is_champion" are the same boolean under two names for two
-- audiences (an MLOps reader expects "champion/candidate"; a
-- versioning-checklist reader expects "is_current"). Documented here
-- rather than duplicating the column.
COMMENT ON COLUMN gold.model_registry.is_champion IS
    'Task 40''s "is_current" flag under this project''s champion/candidate naming (Task 39). At most one TRUE row per (college_key, metric), enforced by ux_model_registry_one_champion.';