"""Model registry + forecast fact tables (champion/candidate workflow)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/008_forecast_registry.sql. model_registry
# is INSERT-only per candidate (an audit trail of every trained model, not
# just winners); fact_forecast is written only by promoted champions, so a
# rejected candidate can never overwrite a good forecast already in
# production. target_period columns are plain values, not an FK into
# gold.dim_academic_period, because that dimension only covers observed
# history (see original .sql comment for the full rationale).
_UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS gold.model_registry (
    model_registry_key   BIGSERIAL PRIMARY KEY,
    college_key           SMALLINT NOT NULL REFERENCES gold.dim_college (college_key),
    metric                 VARCHAR(32) NOT NULL CHECK (metric IN ('enrollment_count', 'graduation_count')),
    model_version           VARCHAR(64) NOT NULL,
    trained_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    mae                       NUMERIC(14, 4) NOT NULL,
    rmse                      NUMERIC(14, 4) NOT NULL,
    mape                      NUMERIC(14, 4),
    r2                        NUMERIC(14, 6) NOT NULL,
    best_baseline_mae         NUMERIC(14, 4) NOT NULL,
    beats_baseline            BOOLEAN NOT NULL,

    is_champion               BOOLEAN NOT NULL DEFAULT FALSE,
    promoted_at                TIMESTAMPTZ,
    rejected_reason            VARCHAR(256),
    artifact_path              VARCHAR(256) NOT NULL,

    UNIQUE (college_key, metric, model_version)
);
CREATE INDEX IF NOT EXISTS ix_model_registry_lookup
    ON gold.model_registry (college_key, metric, trained_at DESC);

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
    model_version                   VARCHAR(64) NOT NULL,

    yhat                             NUMERIC(12, 2) NOT NULL CHECK (yhat >= 0),
    yhat_lower                       NUMERIC(12, 2) NOT NULL,
    yhat_upper                       NUMERIC(12, 2) NOT NULL,

    generated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (college_key, metric, target_period_ordinal, model_version)
);
CREATE INDEX IF NOT EXISTS ix_fact_forecast_lookup
    ON gold.fact_forecast (college_key, metric, target_period_ordinal);
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gold.fact_forecast CASCADE;")
    op.execute("DROP TABLE IF EXISTS gold.model_registry CASCADE;")