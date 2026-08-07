"""Gold fact grain constraints (prevents silent duplicate-fact loads)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Verbatim body of warehouse/ddl/005_gold_fact_constraints.sql. 0003's fact
# tables had no PRIMARY KEY or UNIQUE constraint; Gold facts are rebuilt
# from Silver on every run and loaded via TRUNCATE + append, which is only
# idempotent if the loaded DataFrame never contains duplicate rows at the
# fact's natural grain. These constraints make that grain a database
# -enforced invariant instead of a Python-only assumption.
_UPGRADE_SQL = """
ALTER TABLE gold.fact_enrollment
    ADD CONSTRAINT uq_gold_fact_enrollment_student_period
        UNIQUE (student_key, academic_period_key);

ALTER TABLE gold.fact_graduation
    ADD CONSTRAINT uq_gold_fact_graduation_student
        UNIQUE (student_key);

ALTER TABLE gold.fact_dropout
    ADD CONSTRAINT uq_gold_fact_dropout_student
        UNIQUE (student_key);

ALTER TABLE gold.fact_shifter
    ADD CONSTRAINT uq_gold_fact_shifter_student_period
        UNIQUE (student_key, academic_period_key);

ALTER TABLE gold.fact_retention
    ADD CONSTRAINT uq_gold_fact_retention_student_period
        UNIQUE (student_key, academic_period_key);
"""

_DOWNGRADE_SQL = """
ALTER TABLE gold.fact_retention DROP CONSTRAINT IF EXISTS uq_gold_fact_retention_student_period;
ALTER TABLE gold.fact_shifter DROP CONSTRAINT IF EXISTS uq_gold_fact_shifter_student_period;
ALTER TABLE gold.fact_dropout DROP CONSTRAINT IF EXISTS uq_gold_fact_dropout_student;
ALTER TABLE gold.fact_graduation DROP CONSTRAINT IF EXISTS uq_gold_fact_graduation_student;
ALTER TABLE gold.fact_enrollment DROP CONSTRAINT IF EXISTS uq_gold_fact_enrollment_student_period;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)