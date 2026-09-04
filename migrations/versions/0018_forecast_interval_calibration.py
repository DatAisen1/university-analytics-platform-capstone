"""Persist interval calibration provenance onto gold.fact_forecast

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-04
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

# P1 (forecasting-layer review follow-up, referenced in deploy_forecast.py's
# _build_champion_model docstring as "NOT YET persisted onto
# gold.fact_forecast itself"): fit_prophet() has known, since the P0 Gate
# Follow-Up 22.1 work in train_prophet.py, whether a given Prophet model's
# yhat_lower/yhat_upper came from genuine MCMC sampling or a disclosed
# MAP-only fallback (models.forecasting.train_prophet.IntervalCalibration).
# Until this migration, that fact was only ever logged to console
# (deploy_forecast.py's _build_champion_model) -- it never reached the
# table a dashboard or stakeholder query would actually read. A reader of
# gold.fact_forecast had no way to distinguish a genuinely calibrated 80%
# interval from one this project's own evaluation (0/222 walk-forward
# folds achieved genuine MCMC calibration; see forecasting/artifacts/
# evaluation_report.md) already knows under-covers its stated rate.
#
# Two columns, not one boolean: a bare "is this calibrated" collapses two
# different questions -- (1) which of the three deployable model families
# produced this interval (Prophet, count_model, or a baseline), and (2)
# for Prophet specifically, WHY it landed in the MAP-only branch (short
# training window vs. divergent MCMC sampling) if it did. The four values
# below cover every algorithm deploy_forecast._build_champion_model can
# dispatch to (models.forecasting.deploy_forecast._interval_calibration_for
# is the single place that decides which one applies, kept in sync with
# this CHECK constraint by hand since Postgres has no shared-enum
# mechanism with application code here):
#
#   'bayesian_mcmc'         -- Prophet, genuine MCMC-sampled interval.
#                              Not observed in this project's real data
#                              to date (MCMC_CALIBRATION_ENABLED = False,
#                              see train_prophet.py), but the column
#                              allows for it becoming true again without
#                              a further migration, per that flag's own
#                              "re-activates unchanged" design.
#   'map_disclosed'         -- Prophet, MAP-only fallback. The common
#                              case today. `interval_calibration_note`
#                              carries the specific reason (short training
#                              window vs. MCMC divergence).
#   'count_quantile'        -- count_model's real Poisson/Negative-Binomial
#                              quantile interval. Not MCMC-calibrated, but
#                              not a MAP approximation either -- a distinct,
#                              third thing, so it gets its own value rather
#                              than being folded into either Prophet label.
#                              `interval_calibration_note` carries which
#                              distribution was actually used (e.g.
#                              "negative_binomial_glm(extrapolation-capped)").
#   'degenerate_zero_width' -- naive / historical_avg / seasonal_naive.
#                              yhat_lower = yhat_upper = yhat by deliberate
#                              design (see baselines.py) -- "no uncertainty
#                              quantification available," not a bug.
#
# Nullable, not NOT NULL, for the same reason migration 0009's provenance
# columns are nullable: every row written BEFORE this migration genuinely
# never recorded this, and backfilling a fabricated value would be worse
# than an honest NULL. A dedicated CHECK constraint (below) enforces
# population going forward for program-grain rows specifically -- every
# program-grain INSERT deploy_forecast.py issues after this migration
# supplies both columns, enforced in application code the same way
# TrainingMetadata's four fields are a required constructor argument, not
# just documented here.
#
# Deliberately NOT extended to college/campus rollup rows (migration
# 0016's forecast_grain): a rollup is a SQL SUM over N already-promoted
# program-level forecasts, which may carry DIFFERENT calibration methods
# (e.g. one program's champion is Prophet/map_disclosed, another's is a
# baseline/degenerate_zero_width) -- a single scalar column cannot
# honestly represent that without inventing a "mixed" bucket, which is
# real design work, not a mechanical extension of this fix. Both columns
# stay NULL for 'college' and 'campus' grain rows; scoped as an explicit
# follow-up rather than silently left inconsistent.
_UPGRADE_SQL = """
ALTER TABLE gold.fact_forecast
    ADD COLUMN IF NOT EXISTS interval_calibration_method VARCHAR(32)
        CHECK (interval_calibration_method IN (
            'bayesian_mcmc', 'map_disclosed', 'count_quantile', 'degenerate_zero_width'
        )),
    ADD COLUMN IF NOT EXISTS interval_calibration_note VARCHAR(256);

ALTER TABLE gold.fact_forecast
    ADD CONSTRAINT ck_fact_forecast_program_grain_calibration CHECK (
        forecast_grain != 'program' OR interval_calibration_method IS NOT NULL
    );

COMMENT ON COLUMN gold.fact_forecast.interval_calibration_method IS
    'Provenance of yhat_lower/yhat_upper for this row -- see models.forecasting.deploy_forecast._interval_calibration_for. One of bayesian_mcmc, map_disclosed, count_quantile, degenerate_zero_width. Required (NOT NULL enforced by ck_fact_forecast_program_grain_calibration) for forecast_grain=''program'' rows written after this migration; NULL for pre-migration rows and for college/campus rollup rows (not yet defined for a multi-program sum -- see this migration''s module docstring).';
COMMENT ON COLUMN gold.fact_forecast.interval_calibration_note IS
    'Human-readable detail on interval_calibration_method: for map_disclosed, the specific fallback reason (short training window vs. MCMC divergence, from train_prophet.IntervalCalibration.reason); for count_quantile, which distribution was fit (models.forecasting.count_model.CountModelFit.detail, e.g. "negative_binomial_glm(extrapolation-capped)"); NULL for bayesian_mcmc and degenerate_zero_width, which need no further explanation.';
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE gold.fact_forecast
            DROP CONSTRAINT IF EXISTS ck_fact_forecast_program_grain_calibration,
            DROP COLUMN IF EXISTS interval_calibration_note,
            DROP COLUMN IF EXISTS interval_calibration_method;
    """)