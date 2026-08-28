"""Add forecast_grain to fact_forecast for college/campus bottom-up rollups

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-27
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# DS Evaluation task P1.1-P1.4: the Web Team and university stakeholders
# need enrollment/graduation outcomes at THREE grains -- program, college,
# and campus-wide -- not just program (migration 0013's grain). This is
# NOT a new modeling project: college/campus numbers are bottom-up
# aggregations (sum) of the already-trained, already-promoted
# program-level champion forecasts -- standard hierarchical forecast
# reconciliation, not three independent model families. See
# models/forecasting/rollup_forecast.py for the aggregation logic this
# schema change supports.
#
# Design choice, and why: extend gold.fact_forecast with a forecast_grain
# discriminator column rather than create separate college_forecast /
# campus_forecast tables. A consumer (dashboard, API) should be able to
# query ONE table and filter by grain, not know about three table names
# that must independently stay in sync. The audit-trail blast radius of
# this change is small and was checked before making it: only
# models/forecasting/deploy_forecast.py and its test write/read this
# table (confirmed via `grep -rl fact_forecast tests/ dbt/ pipelines/
# models/`) -- no dbt model or other pipeline stage depends on its exact
# shape.
#
# Two columns that were NOT NULL under program grain become nullable,
# because they don't apply the same way to a derived rollup row:
#
#   program_key: NULL for college/campus rows (there is no single
#   program at that grain -- that's the whole point of a rollup).
#
#   model_registry_key: NULL for college/campus rows. A rollup is a SQL
#   SUM over N already-promoted models, not the output of ONE trained
#   model -- there is no single model_registry_key that correctly
#   attributes it. Provenance for a rollup row instead comes from
#   model_version (see below), not this FK. Forcing a fake/arbitrary
#   model_registry_key onto a rollup row would be actively misleading --
#   it would look like a single trained model produced that number, and
#   deleting that one candidate model row would seem to orphan a rollup
#   it never really depended on. NULL is the honest representation.
#
# The prior single UNIQUE(program_key, metric, target_period_ordinal,
# model_version) constraint cannot express correct uniqueness across
# three grains: NULL columns don't collide under Postgres's default
# UNIQUE semantics (multiple college/campus rows would all have
# program_key = NULL and never conflict with each other, which is NOT
# the uniqueness we want at those grains). Replaced with three grain-
# scoped PARTIAL unique indexes instead, each matched by a corresponding,
# grain-specific ON CONFLICT clause in rollup_forecast.py (Postgres
# requires an ON CONFLICT target to exactly match a partial index's
# columns AND predicate -- there is no single ON CONFLICT clause that
# could target all three).
#
# A CHECK constraint enforces the grain/key-population contract at the
# database level (not just in application code) so a malformed row -- a
# 'college' row with a program_key, or a 'program' row missing one --
# is rejected at INSERT time, not discovered later by a confused
# dashboard query.
_UPGRADE_SQL = """
ALTER TABLE gold.fact_forecast
    ADD COLUMN IF NOT EXISTS forecast_grain VARCHAR(16) NOT NULL DEFAULT 'program'
        CHECK (forecast_grain IN ('program', 'college', 'campus'));

-- Coverage signal, not just cosmetic: a college/campus rollup is a sum
-- over however many programs/colleges actually HAD a promoted model --
-- graduation_count in particular can have entire colleges missing
-- (every one of their programs failed to beat baseline), which makes a
-- campus-wide total look complete while silently excluding real
-- graduates. total_entity_count/covered_entity_count let a consumer
-- compute "3 of 8 colleges covered" themselves rather than trust a
-- number that LOOKS like a full campus total but isn't. NULL for
-- program-grain rows (coverage is trivially 1-of-1, not worth storing).
ALTER TABLE gold.fact_forecast
    ADD COLUMN IF NOT EXISTS covered_entity_count SMALLINT,
    ADD COLUMN IF NOT EXISTS total_entity_count SMALLINT;

ALTER TABLE gold.fact_forecast
    ALTER COLUMN program_key DROP NOT NULL,
    ALTER COLUMN model_registry_key DROP NOT NULL;

-- Dynamic lookup, same reasoning as migration 0013: the auto-generated
-- constraint name is not safe to hardcode (NAMEDATALEN truncation risk).
--
-- BUG FOUND RUNNING THIS MIGRATION (fixed here, disclosed rather than
-- silently patched): migration 0013's version of this pattern compared
-- conkey to array_agg(attnum ORDER BY attnum) -- attnums sorted
-- ascending. That happened to work in 0013 only because, at the time,
-- every column in that particular 4-column constraint had been part of
-- the ORIGINAL table (migration 0008), so their declaration order in
-- the UNIQUE(...) clause coincidentally matched their ascending
-- physical attnum order. It does NOT hold here: program_key was added
-- by migration 0013 itself (an ALTER TABLE ADD COLUMN, so it got the
-- NEXT available attnum, higher than the four original columns) but is
-- declared FIRST in `UNIQUE (program_key, metric, target_period_ordinal,
-- model_version)` -- so the real conkey is {13,3,6,8} (declaration
-- order), not {3,6,8,13} (sorted order). The sorted-array equality check
-- silently matched nothing, this DO block silently did nothing, and the
-- stale constraint was left in place. Confirmed by running this
-- migration against a real database and inspecting pg_constraint
-- directly -- exactly the kind of failure a migration DRY-RUN or CI
-- check would not catch without a real database to run it against.
--
-- Fixed with an order-independent SET comparison (both directions of
-- containment, plus a length check) instead of assuming any particular
-- element order:
DO $$
DECLARE
    old_constraint_name text;
    target_attnums int[];
BEGIN
    SELECT array_agg(attnum) INTO target_attnums
    FROM pg_attribute
    WHERE attrelid = 'gold.fact_forecast'::regclass
      AND attname IN ('program_key', 'metric', 'target_period_ordinal', 'model_version');

    SELECT conname INTO old_constraint_name
    FROM pg_constraint
    WHERE conrelid = 'gold.fact_forecast'::regclass
      AND contype = 'u'
      AND conkey::int[] <@ target_attnums
      AND target_attnums <@ conkey::int[]
      AND array_length(conkey, 1) = 4;

    IF old_constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE gold.fact_forecast DROP CONSTRAINT %I', old_constraint_name);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_forecast_program_grain
    ON gold.fact_forecast (program_key, metric, target_period_ordinal, model_version)
    WHERE forecast_grain = 'program';

CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_forecast_college_grain
    ON gold.fact_forecast (college_key, metric, target_period_ordinal, model_version)
    WHERE forecast_grain = 'college';

CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_forecast_campus_grain
    ON gold.fact_forecast (metric, target_period_ordinal, model_version)
    WHERE forecast_grain = 'campus';

ALTER TABLE gold.fact_forecast
    ADD CONSTRAINT ck_fact_forecast_grain_keys CHECK (
        (forecast_grain = 'program' AND program_key IS NOT NULL AND college_key IS NOT NULL AND model_registry_key IS NOT NULL)
        OR (forecast_grain = 'college' AND program_key IS NULL AND college_key IS NOT NULL AND model_registry_key IS NULL
            AND covered_entity_count IS NOT NULL AND total_entity_count IS NOT NULL)
        OR (forecast_grain = 'campus' AND program_key IS NULL AND college_key IS NULL AND model_registry_key IS NULL
            AND covered_entity_count IS NOT NULL AND total_entity_count IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS ix_fact_forecast_grain_lookup
    ON gold.fact_forecast (forecast_grain, metric, target_period_ordinal);

COMMENT ON COLUMN gold.fact_forecast.forecast_grain IS
    'program: one trained Prophet model per (program, metric), model_registry_key populated. college/campus: bottom-up SUM of program-grain rows -- see models/forecasting/rollup_forecast.py -- model_registry_key is NULL by design (a rollup is not one trained model''s output); provenance is model_version instead.';
"""

_DOWNGRADE_SQL = """
DELETE FROM gold.fact_forecast WHERE forecast_grain != 'program';

ALTER TABLE gold.fact_forecast DROP CONSTRAINT IF EXISTS ck_fact_forecast_grain_keys;
DROP INDEX IF EXISTS gold.ix_fact_forecast_grain_lookup;
DROP INDEX IF EXISTS gold.ux_fact_forecast_campus_grain;
DROP INDEX IF EXISTS gold.ux_fact_forecast_college_grain;
DROP INDEX IF EXISTS gold.ux_fact_forecast_program_grain;

ALTER TABLE gold.fact_forecast
    ALTER COLUMN program_key SET NOT NULL,
    ALTER COLUMN model_registry_key SET NOT NULL,
    ADD CONSTRAINT uq_fact_forecast_program_metric_period_version
        UNIQUE (program_key, metric, target_period_ordinal, model_version);

ALTER TABLE gold.fact_forecast
    DROP COLUMN IF EXISTS forecast_grain,
    DROP COLUMN IF EXISTS covered_entity_count,
    DROP COLUMN IF EXISTS total_entity_count;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)