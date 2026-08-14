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
    the caller already opened -- no second connection, no connection leak.

    P0.51 bug fix: `conn` is NOT always a true raw psycopg2 connection.
    Both load_gold_to_postgres.py and load_silver_to_postgres.py call
    `assert_up_to_date(engine.raw_connection())` -- and despite the name,
    SQLAlchemy's `Engine.raw_connection()` returns a pooled proxy
    (`_ConnectionFairy`), not the underlying DBAPI connection. Handing
    that proxy to `creator=lambda: conn` made the psycopg2 dialect's
    on_connect hook call `psycopg2.extras.register_uuid(None, <Fairy>)`
    on THIS new engine's first checkout, which crashes with
    `TypeError: argument 2 must be a connection, cursor or None` --
    psycopg2's C extension requires an actual connection/cursor object,
    and a Python-level proxy fails that check even though it duck-types
    as one everywhere else. Concretely: this meant EVERY call to
    load_gold_to_postgres()/load_silver_to_postgres() crashed before
    ever reaching the actual data load, on any real Postgres backend --
    not a rerun-only issue, a first-run-only issue, the exact class of
    bug P0.51 exists to catch, just one layer up from where the previous
    fix (replace_all_table_contents) was looking.

    `.driver_connection` unwraps a pool proxy to the true DBAPI
    connection; a genuine raw psycopg2 connection (the pre-Alembic
    call sites, and any future direct caller) doesn't have that
    attribute, so it's returned unchanged via getattr's default.
    """
    dbapi_conn = getattr(conn, "driver_connection", conn)
    return create_engine("postgresql+psycopg2://", creator=lambda: dbapi_conn)


_EXPECTED_SCHEMAS = ("bronze", "silver", "gold", "marts", "meta")


def _expected_schemas_missing(connection) -> List[str]:
    """P0.54 fix: `alembic_version` lives in `public`, which is NOT one
    of this project's own schemas (bronze/silver/gold/marts/meta) and is
    therefore easy to leave behind by accident when "cleaning" a
    database -- e.g. a manual `DROP SCHEMA bronze, silver, gold, marts,
    meta CASCADE` (exactly what
    tests/integration/test_warehouse_rebuild_from_clean.py's own
    clean_database fixture did, and a real operator could just as
    plausibly do by hand during a partial recovery). When that happens,
    Alembic still finds its version row in `public.alembic_version`,
    concludes "already at head," and silently does nothing -- leaving
    every actual table gone with no error at all. That's a genuine
    disaster-recovery hazard, not just a test-fixture bug: the entire
    point of P0.54 ("verify clean rebuild... database deletion...
    pipeline rerun") is that this exact sequence must either fully
    rebuild or fail loudly, never silently leave you with nothing.
    """
    from sqlalchemy import text

    existing = {
        row[0]
        for row in connection.execute(
            text("SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(:names)"),
            {"names": list(_EXPECTED_SCHEMAS)},
        )
    }
    return [name for name in _EXPECTED_SCHEMAS if name not in existing]


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

        script = ScriptDirectory.from_config(cfg)
        target_heads = set(script.get_heads())
        if before == target_heads:
            # Alembic considers this a no-op upgrade. Before trusting
            # that, confirm the schemas its own migrations are supposed
            # to have created actually exist -- see
            # _expected_schemas_missing's docstring for why "Alembic
            # says up to date" and "the database is actually correct"
            # can silently diverge.
            missing = _expected_schemas_missing(connection)
            if missing:
                raise MigrationError(
                    f"alembic_version reports the database is already at head "
                    f"{sorted(before)}, but expected schema(s) {missing} do not "
                    f"exist. This means alembic_version survived a schema "
                    f"drop/reset that the rest of the database did not -- "
                    f"Alembic will NOT re-run migrations it believes are "
                    f"already applied. Drop/reset `public.alembic_version` (or "
                    f"the whole database) before re-bootstrapping, don't rely "
                    f"on dropping bronze/silver/gold/marts/meta alone."
                )

        cfg.attributes["connection"] = connection
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            raise MigrationError(f"Alembic upgrade failed: {exc}") from exc

        # Bug found running migration 0012 for the first time through this
        # function: SQLAlchemy 2.0-style Connection objects require an
        # EXPLICIT commit -- unlike the old implicit-autocommit-on-close
        # behavior, `with engine.connect() as connection:` silently rolls
        # back any uncommitted transaction the moment this block exits.
        # Alembic's own command.upgrade() logs "Running upgrade..." and
        # this function happily returned the newly-applied revision list,
        # making it LOOK like the migration succeeded -- but without this
        # commit, every change (including the write to alembic_version
        # itself) was discarded on connection close. Confirmed against a
        # live Postgres 16 instance: 0012 printed as applied, then
        # `SELECT version_num FROM public.alembic_version` still showed
        # the previous revision. Migrations 0001-0011 are unaffected only
        # because they were applied through a different, correctly-
        # committing path at some earlier point -- this function itself
        # has never durably committed anything until now.
        connection.commit()

        after = set(context.get_current_heads())
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