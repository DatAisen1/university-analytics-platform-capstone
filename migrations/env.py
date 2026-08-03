
"""
migrations/env.py

Standard Alembic env.py, with one deliberate deviation: `target_metadata`
is NOT wired up for `--autogenerate`. This project's DDL includes partial
indexes (WHERE _is_current, WHERE is_champion), DO $$ role-safe blocks,
and CHECK (metric IN (...)) constraints that Alembic's autogenerate
cannot reliably diff against ORM models -- attempting to rely on
autogenerate here risks silently DROPPING a hand-tuned constraint the
next time someone runs `alembic revision --autogenerate`. Every
migration in versions/ is therefore hand-authored (ported directly from
warehouse/ddl/*.sql, which this replaces as the source of truth), and
warehouse/models/ exists purely as a typed query layer, checked against
migrations manually in code review -- not generated from or generating
the schema.

Two ways this runs:
  1. CLI: `alembic upgrade head` -- uses sqlalchemy.url from alembic.ini
     / the ALEMBIC_DATABASE_URL env var (operator/CI use).
  2. Programmatically, reusing an EXISTING psycopg2 connection (how
     pipelines.common.migrations.apply_migrations() calls this, so
     get_admin_connection()'s already-open connection is reused rather
     than opening a second one) -- see config.attributes["connection"].
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# CLI use: allow overriding the URL via env var without editing alembic.ini
# (mirrors pipelines/common/postgres.py's POSTGRES_* env-var convention).
db_url = os.environ.get("ALEMBIC_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = None  # deliberate -- see module docstring


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse an existing connection if the caller supplied one (see
    # pipelines/common/migrations.py) -- this is the standard Alembic
    # pattern for "run migrations as part of an existing app/test
    # connection" instead of opening a brand-new engine.
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()