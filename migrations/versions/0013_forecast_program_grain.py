"""Repoint model_registry / fact_forecast from college grain to program grain

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-14
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# P1 (Data Science Recovery) fix: gold.model_registry and gold.fact_forecast
# were built keyed on college_key -- one Prophet series per (college, metric)
# -- while models.forecasting.train_prophet.load_series() queried
# gold.fact_institution_kpi directly with hand-rolled SQL, never touching
# gold.ml_program_forecast_features (pipelines/gold/build_ml_features.py,
# Task 31-33's dedicated, leakage-safe, fingerprinted forecast dataset,
# built at PROGRAM grain and never consumed by anything until now).
#
# This migration moves the forecast/registry grain to match the dataset
# that was actually built for this purpose: one series per
# (program, metric), not (college, metric). college_key is KEPT on both
# tables as a denormalized, FK-enforced convenience column (sourced from
# gold.dim_program.college_key at write time) so a dashboard can still
# filter/rollup by college without a join -- the same denormalization
# pattern this project already uses for model_version on fact_forecast.
#
# BREAKING CHANGE, disclosed rather than silently worked around: every
# existing row in both tables was trained/recorded at college grain and
# has no valid program_key to backfill (a college has many programs; the
# mapping is not 1:1 and cannot be reconstructed after the fact). Both
# tables are TRUNCATEd as part of this migration -- the champion/candidate
# history and any previously-written forecasts are intentionally not
# preserved across the grain change. This is safe: nothing outside this
# module's own audit trail depends on the pre-migration rows (confirmed:
# no dbt model or other pipeline stage reads gold.model_registry or
# gold.fact_forecast), and re-running the deploy_forecast asset rebuilds
# both tables' history at the new, correct grain from scratch.
#
# college_key already exists on both tables pre-migration, so this
# migration ALTERs it into a denormalized, non-grain-key column rather
# than dropping and re-adding it.
_UPGRADE_SQL = """
-- Both tables in ONE TRUNCATE statement: Postgres refuses to truncate a
-- table referenced by a FK (model_registry, referenced by
-- fact_forecast.model_registry_key) unless the referencing table is
-- truncated in the SAME statement -- truncating fact_forecast first in
-- a separate statement does not satisfy this, even though it leaves
-- model_registry with zero real dependents by the time the second
-- statement runs. Postgres checks the constraint graph, not row counts.
TRUNCATE TABLE gold.fact_forecast, gold.model_registry;

-- model_registry: drop the college-grain uniqueness/champion constraints,
-- add program_key as the new grain key, keep college_key as a
-- denormalized convenience column (nullable dropped once populated by
-- every future INSERT -- application code always supplies it).
--
-- DROP INDEX targets are schema-qualified (gold.xxx), unlike a bare
-- name. DROP INDEX resolves an unqualified name against the session's
-- search_path, NOT against the table it's an index on -- every other
-- statement in this file schema-qualifies via "gold.model_registry"
-- (a table reference, resolved differently), which masked this until
-- it was actually run against a session whose search_path didn't
-- include gold: DROP INDEX IF EXISTS ux_model_registry_one_champion
-- silently matched nothing, left the old college-grain index in place,
-- and the later CREATE UNIQUE INDEX of the same name then collided
-- with it.
--
-- The old (college_key, metric, model_version) UNIQUE constraint's name
-- is Postgres's auto-generated default, which is NOT safe to hardcode
-- here: Postgres truncates auto-generated constraint names that exceed
-- NAMEDATALEN (63 bytes), so guessing the literal name risks silently
-- matching nothing. Looked up dynamically via pg_constraint instead.
DO $$
DECLARE
    old_constraint_name text;
BEGIN
    SELECT conname INTO old_constraint_name
    FROM pg_constraint
    WHERE conrelid = 'gold.model_registry'::regclass
      AND contype = 'u'
      AND conkey = (
          SELECT array_agg(attnum ORDER BY attnum)
          FROM pg_attribute
          WHERE attrelid = 'gold.model_registry'::regclass
            AND attname IN ('college_key', 'metric', 'model_version')
      );
    IF old_constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE gold.model_registry DROP CONSTRAINT %I', old_constraint_name);
    END IF;
END $$;

DROP INDEX IF EXISTS gold.ux_model_registry_one_champion;
ALTER TABLE gold.model_registry
    ADD COLUMN IF NOT EXISTS program_key INTEGER REFERENCES gold.dim_program (program_key);
ALTER TABLE gold.model_registry
    ALTER COLUMN college_key DROP NOT NULL;

ALTER TABLE gold.model_registry
    ALTER COLUMN program_key SET NOT NULL,
    ADD CONSTRAINT uq_model_registry_program_metric_version UNIQUE (program_key, metric, model_version);

CREATE UNIQUE INDEX ux_model_registry_one_champion
    ON gold.model_registry (program_key, metric)
    WHERE is_champion;

DROP INDEX IF EXISTS gold.ix_model_registry_lookup;
CREATE INDEX ix_model_registry_lookup
    ON gold.model_registry (program_key, metric, trained_at DESC);

COMMENT ON COLUMN gold.model_registry.program_key IS
    'P1 fix: the actual forecast grain (Prophet trains one series per (program, metric), not (college, metric)) -- see migration docstring.';
COMMENT ON COLUMN gold.model_registry.college_key IS
    'Denormalized from dim_program.college_key at write time for college-level filtering/rollup without a join. NOT the grain key -- see program_key.';

-- fact_forecast: same grain change, same dynamic-lookup reasoning as above
-- (this constraint spans FOUR columns, well past the point where
-- hardcoding Postgres's default auto-generated name is safe).
DO $$
DECLARE
    old_constraint_name text;
BEGIN
    SELECT conname INTO old_constraint_name
    FROM pg_constraint
    WHERE conrelid = 'gold.fact_forecast'::regclass
      AND contype = 'u'
      AND conkey = (
          SELECT array_agg(attnum ORDER BY attnum)
          FROM pg_attribute
          WHERE attrelid = 'gold.fact_forecast'::regclass
            AND attname IN ('college_key', 'metric', 'target_period_ordinal', 'model_version')
      );
    IF old_constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE gold.fact_forecast DROP CONSTRAINT %I', old_constraint_name);
    END IF;
END $$;

ALTER TABLE gold.fact_forecast
    ADD COLUMN IF NOT EXISTS program_key INTEGER REFERENCES gold.dim_program (program_key);
ALTER TABLE gold.fact_forecast
    ALTER COLUMN college_key DROP NOT NULL;

ALTER TABLE gold.fact_forecast
    ALTER COLUMN program_key SET NOT NULL,
    ADD CONSTRAINT uq_fact_forecast_program_metric_period_version
        UNIQUE (program_key, metric, target_period_ordinal, model_version);

DROP INDEX IF EXISTS gold.ix_fact_forecast_lookup;
CREATE INDEX ix_fact_forecast_lookup
    ON gold.fact_forecast (program_key, metric, target_period_ordinal);

COMMENT ON COLUMN gold.fact_forecast.program_key IS
    'P1 fix: the actual forecast grain -- see gold.model_registry.program_key and migration 0013 docstring.';
COMMENT ON COLUMN gold.fact_forecast.college_key IS
    'Denormalized from dim_program.college_key at write time. NOT the grain key -- see program_key.';
"""

_DOWNGRADE_SQL = """
ALTER TABLE gold.fact_forecast
    DROP CONSTRAINT IF EXISTS uq_fact_forecast_program_metric_period_version;
DROP INDEX IF EXISTS gold.ix_fact_forecast_lookup;
CREATE INDEX ix_fact_forecast_lookup ON gold.fact_forecast (college_key, metric, target_period_ordinal);
ALTER TABLE gold.fact_forecast
    ALTER COLUMN college_key SET NOT NULL,
    DROP COLUMN IF EXISTS program_key;
ALTER TABLE gold.fact_forecast
    ADD CONSTRAINT fact_forecast_college_key_metric_target_period_ordinal_key
        UNIQUE (college_key, metric, target_period_ordinal, model_version);

DROP INDEX IF EXISTS gold.ux_model_registry_one_champion;
ALTER TABLE gold.model_registry
    DROP CONSTRAINT IF EXISTS uq_model_registry_program_metric_version;
DROP INDEX IF EXISTS gold.ix_model_registry_lookup;
CREATE INDEX ix_model_registry_lookup ON gold.model_registry (college_key, metric, trained_at DESC);
ALTER TABLE gold.model_registry
    ALTER COLUMN college_key SET NOT NULL,
    DROP COLUMN IF EXISTS program_key;
ALTER TABLE gold.model_registry
    ADD CONSTRAINT model_registry_college_key_metric_model_version_key
        UNIQUE (college_key, metric, model_version);
CREATE UNIQUE INDEX ux_model_registry_one_champion
    ON gold.model_registry (college_key, metric)
    WHERE is_champion;

TRUNCATE TABLE gold.fact_forecast, gold.model_registry;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)