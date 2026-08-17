"""Model registry provenance: dataset_fingerprint

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-17
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# P1 (Forecast Output Contract, "recommended metadata": dataset_fingerprint).
# pipelines/gold/build_ml_features.py::feature_dataset_fingerprint() has
# existed since Task 31-33 and was already surfaced in Dagster asset
# metadata (orchestration/assets.py), but was never captured anywhere a
# trained candidate could be traced back to it -- a forecast row could be
# joined to its model_registry row for algorithm/training window/eval
# metrics (migration 0009), but not to the exact snapshot of
# gold.ml_program_forecast_features that produced it. This closes that
# gap the same way 0009 did: an additive, nullable column (old rows
# genuinely never recorded this; no fabricated backfill), populated by
# application code on every future INSERT (model_registry.TrainingMetadata
# is a required argument to record_candidate(), enforced there -- not
# just documented here).
_UPGRADE_SQL = """
ALTER TABLE gold.model_registry
    ADD COLUMN IF NOT EXISTS dataset_fingerprint VARCHAR(64);

COMMENT ON COLUMN gold.model_registry.dataset_fingerprint IS
    'pipelines.gold.build_ml_features.feature_dataset_fingerprint() value for the gold.ml_program_forecast_features snapshot this candidate was trained against -- lets a forecast row (joined via model_registry_key) be traced back to the exact feature-dataset version that produced it, not just the training window it covered.';
"""

_DOWNGRADE_SQL = """
ALTER TABLE gold.model_registry
    DROP COLUMN IF EXISTS dataset_fingerprint;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)