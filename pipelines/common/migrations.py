"""
pipelines/common/migrations.py

Task 59 update: migrations are now run by Alembic against
migrations/versions/ (ported 1:1 from warehouse/ddl/*.sql -- see that
directory's git history / migrations/versions/000N_*.py docstrings for
the original per-migration rationale). This module is now a thin
adapter that PRESERVES apply_migrations() and assert_up_to_date()'s
original signatures, so pipelines/gold/load_gold_to_postgres.py,
pipelines/silver/load_silver_to_postgres.py, and
pipelines/common/postgres.py -- all three of which import these two
functions by name -- require NO changes.

Why keep the psycopg2-connection-based signature instead of switching
callers to a SQLAlchemy engine: alembic can run against ANY existing
DBAPI connection via a `creator` callable, so preserving the existing
`conn` parameter (a raw psycopg2 connection, exactly as before) avoids
forcing three unrelated call sites to change their connection-management
code just because the migration *runner* changed underneath them --
that would be an unrequested refactor.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from pipelines.common.errors import PostgresError

_REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = _REPO_ROOT / "warehouse" / "ddl"  # retained only so any lingering import doesn't hard-crash; unused by this module now
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"


class MigrationError(PostgresError):
    """Base class for migration-runner failures (unchanged from the pre-Alembic version)."""

    def __init__(self, message: str, *, stage: str = "Postgres Migrations", **kwargs):
        super().__init__(message, stage=stage, **kwargs)


class MigrationChecksumError(MigrationError):
    """Raised when Alembic's own version history and the versions/ directory
    on disk disagree in a way Alembic itself flags (e.g. multiple heads) --
    the append-only guarantee is now enforced by Alembic rather than the
    custom checksum table this project used before."""


def _alembic_config(ddl_dir: Optional[Path] = None) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return cfg


def _engine_for_connection(conn):
    """Wrap an EXISTING psycopg2 connection in a SQLAlchemy Engine via the
    `creator` hook, so Alembic operates on the same transaction/connection
    the caller already opened -- no second connection, no connection leak."""
    return create_engine("postgresql+psycopg2://", creator=lambda: conn)


def apply_migrations(conn, ddl_dir: Path = DDL_DIR, dry_run: bool = False) -> List[str]:
    """Apply every unapplied Alembic migration against `conn`. Signature
    unchanged from the pre-Alembic version (ddl_dir kept for call-site
    compatibility; unused now that migrations/versions/ is the source of
    truth). Returns the list of revision ids newly applied.
    """
    cfg = _alembic_config()
    engine = _engine_for_connection(conn)

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        before = set(context.get_current_heads())

        if dry_run:
            script = ScriptDirectory.from_config(cfg)
            pending = [r.revision for r in script.walk_revisions() if r.revision not in before]
            return pending

        cfg.attributes["connection"] = connection
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            raise MigrationError(f"Alembic upgrade failed: {exc}") from exc

        after = set(context.get_current_heads())
        script = ScriptDirectory.from_config(cfg)
        newly_applied = [
            r.revision for r in script.walk_revisions()
            if r.revision in after and r.revision not in before
        ]
        return newly_applied


def assert_up_to_date(conn, ddl_dir: Path = DDL_DIR) -> None:
    """Raise MigrationError if any migration hasn't been applied to `conn`.
    Same call-site contract as before: used as a hard guard in
    load_gold_to_postgres.py / load_silver_to_postgres.py before writes.
    """
    cfg = _alembic_config()
    engine = _engine_for_connection(conn)
    script = ScriptDirectory.from_config(cfg)
    target_heads = set(script.get_heads())

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_heads = set(context.get_current_heads())

    pending = target_heads - current_heads
    if pending:
        raise MigrationError(
            f"{len(pending)} migration(s) have not been applied: {sorted(pending)}. "
            f"Run pipelines.common.migrations.apply_migrations() (or `alembic upgrade head`) "
            f"before writing to the warehouse."
        )