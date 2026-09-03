"""P2 KPI Redesign: split enrollment_stability and shifter_stability,
rename the composite to institutional_success_index

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-03
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# See pipelines/gold/build_kpi.py's module docstring for the full
# rationale. Summary of the three changes to gold.fact_institution_kpi:
#
# 1. `enrollment_stability` (magnitude-only, "higher = more stable")
#    is DROPPED and replaced by two columns that don't throw away the
#    sign of the change:
#      - enrollment_growth: signed % change, informational only.
#      - enrollment_volatility: the old magnitude-only computation,
#        renamed to say what it measures. Feeds the composite (inverted).
#
# 2. `shifter_count` (which only ever counted students LEAVING a
#    college, and incorrectly included same-college program switches)
#    is RENAMED to `outgoing_shift_count` and its underlying pipeline
#    computation is corrected to cross-college moves only -- this is a
#    real value change, not just a rename, so pre-migration and
#    post-migration counts for the same historical period will differ.
#    Two new columns complete the split: `incoming_shift_count` and
#    `net_shift_flow` (= incoming - outgoing, informational only).
#
# 3. `success_rate` -> `institutional_success_index`, a genuine schema
#    rename (not a display-layer relabel) so the column name matches
#    what every consumer (dashboard, dbt marts) actually calls it.
#
# gold.fact_institution_kpi is a fully pipeline-computed analytical
# table (rebuilt wholesale by pipelines/gold/build_kpi.py, never
# hand-edited) -- there is no meaningful backfill for the three new
# columns from existing data, since incoming/outgoing shifts and signed
# growth were never captured separately before this migration. New
# columns are added NOT NULL DEFAULT 0 to satisfy the constraint against
# any pre-existing rows, then the default is immediately dropped so a
# future INSERT that forgets to supply a value fails loudly instead of
# silently writing a meaningless zero (see this repo's "fail loudly on
# upstream failure" pipeline convention). Operators must re-run the gold
# KPI build (`python -m pipelines.gold.build_kpi`) after this migration
# to get real, non-placeholder values into every row.
_UPGRADE_SQL = """
ALTER TABLE gold.fact_institution_kpi
    RENAME COLUMN shifter_count TO outgoing_shift_count;

ALTER TABLE gold.fact_institution_kpi
    RENAME COLUMN success_rate TO institutional_success_index;

ALTER TABLE gold.fact_institution_kpi
    ADD COLUMN incoming_shift_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN net_shift_flow INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN enrollment_growth NUMERIC(8, 5) NOT NULL DEFAULT 0,
    ADD COLUMN enrollment_volatility NUMERIC(6, 5) NOT NULL DEFAULT 0;

ALTER TABLE gold.fact_institution_kpi
    ALTER COLUMN incoming_shift_count DROP DEFAULT,
    ALTER COLUMN net_shift_flow DROP DEFAULT,
    ALTER COLUMN enrollment_growth DROP DEFAULT,
    ALTER COLUMN enrollment_volatility DROP DEFAULT;

ALTER TABLE gold.fact_institution_kpi
    DROP COLUMN enrollment_stability;

COMMENT ON COLUMN gold.fact_institution_kpi.outgoing_shift_count IS
    'Cross-college shifts OUT of this college this period. Same-college program switches are excluded (see migration 0017).';
COMMENT ON COLUMN gold.fact_institution_kpi.incoming_shift_count IS
    'Cross-college shifts INTO this college this period.';
COMMENT ON COLUMN gold.fact_institution_kpi.net_shift_flow IS
    'incoming_shift_count - outgoing_shift_count. Informational only -- does not feed institutional_success_index.';
COMMENT ON COLUMN gold.fact_institution_kpi.enrollment_growth IS
    'Signed period-over-period %% change in enrollment_count. Informational only -- does not feed institutional_success_index.';
COMMENT ON COLUMN gold.fact_institution_kpi.enrollment_volatility IS
    'Magnitude-only period-over-period %% change in enrollment_count, clipped to [0,1]. Feeds institutional_success_index (inverted).';
"""

_DOWNGRADE_SQL = """
ALTER TABLE gold.fact_institution_kpi
    ADD COLUMN enrollment_stability NUMERIC(6, 5) NOT NULL DEFAULT 1;

ALTER TABLE gold.fact_institution_kpi
    ALTER COLUMN enrollment_stability DROP DEFAULT;

ALTER TABLE gold.fact_institution_kpi
    DROP COLUMN incoming_shift_count,
    DROP COLUMN net_shift_flow,
    DROP COLUMN enrollment_growth,
    DROP COLUMN enrollment_volatility;

ALTER TABLE gold.fact_institution_kpi
    RENAME COLUMN outgoing_shift_count TO shifter_count;

ALTER TABLE gold.fact_institution_kpi
    RENAME COLUMN institutional_success_index TO success_rate;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)