"""Model registry provenance columns: algorithm, training window, record count

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/009_model_versioning_fields.sql. Nullable,
# not NOT NULL: rows written before this migration genuinely never
# recorded this provenance, and backfilling a fabricated value would be
# worse than an honest NULL. Every row inserted after this migration
# populates all four columns -- enforced in application code
# (model_registry.TrainingMetadata is a required argument to
# record_candidate()), not just documented here.
_UPGRADE_SQL = """
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
    'Row count of the training DataFrame actually fit -- observational/diagnostic only. NOT used as a retraining trigger by itself: a changed count within an already-trained period window (e.g. late corrections/backfill) must not, on its own, cause a retrain.';

COMMENT ON COLUMN gold.model_registry.is_champion IS
    'Task 40''s "is_current" flag under this project''s champion/candidate naming (Task 39). At most one TRUE row per (college_key, metric), enforced by ux_model_registry_one_champion.';
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE gold.model_registry
            DROP COLUMN IF EXISTS training_record_count,
            DROP COLUMN IF EXISTS training_data_end_period_ordinal,
            DROP COLUMN IF EXISTS training_data_start_period_ordinal,
            DROP COLUMN IF EXISTS algorithm;
    """)