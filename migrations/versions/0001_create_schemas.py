"""Create bronze/silver/gold/marts/meta schemas

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze;")
    op.execute("CREATE SCHEMA IF NOT EXISTS silver;")
    op.execute("CREATE SCHEMA IF NOT EXISTS gold;")
    op.execute("CREATE SCHEMA IF NOT EXISTS marts;")
    op.execute("CREATE SCHEMA IF NOT EXISTS meta;")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS marts CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS gold CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS silver CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS bronze CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS meta CASCADE;")