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
  1. CLI: `alembic upgrade head` -- sqlalchemy.url is derived from
     PostgresSettings (pipelines.common.settings), the SAME .env-aware,
     validated config layer every other admin connection in this repo
     already uses (see pipelines.common.postgres.get_admin_connection).
     Previously this read a separate ALEMBIC_DATABASE_URL env var via a
     bare os.environ.get() call that never loaded .env at all -- so it
     silently fell through to alembic.ini's literal placeholder
     ("driver://user:pass@localhost/dbname") unless a developer had
     ALEMBIC_DATABASE_URL exported in their actual shell, producing
     `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:driver`
     with no indication .env was the problem. ALEMBIC_DATABASE_URL had
     zero other consumers in this repo -- removed rather than fixed in
     place, so there's one source of truth for these credentials, not two.
  2. Programmatically, reusing an EXISTING psycopg2 connection (how
     pipelines.common.migrations.apply_migrations() calls this, so
     get_admin_connection()'s already-open connection is reused rather
     than opening a second one) -- see config.attributes["connection"].
     This path never touches sqlalchemy.url or PostgresSettings at all.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # deliberate -- see module docstring


def _db_url_from_settings() -> str:
    """Builds the Alembic connection URL from PostgresSettings, lazily --
    only called from the branches below that actually lack an existing
    connection (never at module import time), so the programmatic path
    (apply_migrations(), which always supplies config.attributes
    ["connection"]) never depends on POSTGRES_*/admin credentials being
    present in the real process environment, e.g. under a test's env={}
    fixture that doesn't set them.
    """
    from pipelines.common.settings import get_postgres_settings

    settings = get_postgres_settings().require_admin_credentials()
    return (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url_from_settings(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse an existing connection if the caller supplied one (see
    # pipelines/common/migrations.py) -- this is the standard Alembic
    # pattern for "run migrations as part of an existing app/test
    # connection" instead of opening a brand-new engine.
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        connectable = create_engine(_db_url_from_settings(), poolclass=pool.NullPool)
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