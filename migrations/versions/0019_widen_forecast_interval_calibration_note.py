"""Widen gold.fact_forecast.interval_calibration_note to TEXT

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-04
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# Bug fix, found in a real pipeline run within hours of migration 0018
# shipping: `interval_calibration_note VARCHAR(256)` was sized without
# actually measuring the real strings this column has to hold.
# train_prophet.py's IntervalCalibration.reason for the project-wide
# MCMC-disabled disclosure (the one that fires on essentially every
# Prophet-champion forecast, since MCMC_CALIBRATION_ENABLED = False is
# the default -- not a rare edge case) is 389 characters, comfortably
# past the 256 limit. Every INSERT deploy_forecast.py issued for a
# Prophet champion hit `psycopg2.errors.StringDataRightTruncation` at
# the Forecast Deployment stage.
#
# Fixed as a NEW migration rather than by rewriting 0018 in place:
# 0018 has already been applied to at least one real database by the
# time this bug surfaced (`alembic upgrade head` was run against it and
# failed downstream, at the forecast-write step, not at the migration
# itself -- the column was created successfully, just too narrow). Once
# a revision has been applied anywhere, its recorded DDL and the file's
# DDL must keep agreeing, or `alembic history`/`downgrade` silently lie
# about what a given revision actually did -- exactly the failure mode
# this project's own README Troubleshooting table warns never to
# hand-patch around (`alembic_version` says one thing, the database
# schema is genuinely something else). Two more contiguous revisions
# apply cleanly for both a fresh clone (0018 then 0019, in order) and
# an already-migrated database (`alembic upgrade head` from 0018 picks
# up 0019 and fixes the column in place) -- no manual `ALTER TABLE`,
# no `alembic_version` surgery, no drift between file and database.
#
# TEXT, not a larger VARCHAR(N): interval_calibration_note is genuinely
# free-text diagnostic content (the specific MCMC-disqualification
# reason, or a count_model fit's composable detail suffixes -- see
# migration 0018's own docstring), not a bounded business value the way
# interval_calibration_method's four-value enum is (left at VARCHAR(32)
# unchanged by this migration -- that field never had this problem).
# Postgres's TEXT has no meaningful storage or performance cost over
# VARCHAR(n) -- picking a new fixed cap here (512? 1024?) would just
# defer the identical bug to the next time a reason string grows.
_UPGRADE_SQL = """
ALTER TABLE gold.fact_forecast
    ALTER COLUMN interval_calibration_note TYPE TEXT;
"""

# Downgrade attempts to narrow back to VARCHAR(256). Deliberately NOT a
# silent truncation: Postgres's `ALTER COLUMN ... TYPE varchar(256)`
# raises `StringDataRightTruncation` and aborts the migration if any
# existing row's note is already longer than 256 characters -- which
# real data will be, post-fix, since this bug's own 389-character
# reason string is exactly what a Prophet champion writes by default.
# That failure is the correct outcome: a downgrade that silently
# discarded real diagnostic data would be worse than the original bug,
# and "loudly refuse rather than silently truncate" is this migration's
# entire reason for existing in the first place.
_DOWNGRADE_SQL = """
ALTER TABLE gold.fact_forecast
    ALTER COLUMN interval_calibration_note TYPE VARCHAR(256);
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
