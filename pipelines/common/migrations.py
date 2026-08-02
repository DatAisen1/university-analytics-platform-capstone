"""
pipelines/common/migrations.py

Task 25 (Fix Database Constraints) fix, root cause:
pipelines/common/postgres.py::apply_schema_ddl() used to run exactly two
hardcoded files (001_create_schemas.sql, 002_grants.sql). Every other DDL
file in warehouse/ddl/ -- including 003_gold_star_schema.sql, which
defines every PK/FK/UNIQUE/index/NOT NULL for the Gold star schema -- was
never executed by any code path. On a clean database, Gold tables were
therefore first created by pandas' `df.to_sql(if_exists="replace")`
inside pipelines/common/postgres.py::replace_table_contents(), which
creates a bare table with ZERO constraints. That is the exact mechanism
that produced missing constraints like uq_gold_dim_program_program_code
and uq_silver_programs_program_code: the SQL that defines them existed
in the repo, but nothing ever ran it against a real database.

This module makes migrations a first-class, ordered, idempotent concept:
  - Every file in warehouse/ddl/ matching `NNN_*.sql` is a migration,
    ordered by its numeric prefix.
  - meta.schema_migrations (000_schema_migrations.sql) tracks which
    migrations have already been applied, keyed by version + checksum.
  - apply_migrations() is safe to call on every deploy/every test run:
    already-applied migrations are skipped; only new ones run, each in
    its own transaction.
  - A migration whose file contents changed after being applied raises
    loudly (MigrationChecksumError) instead of silently re-running or
    silently skipping -- DDL history must be append-only, like any other
    migration tool (Alembic/Flyway) enforces.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import List, NamedTuple, Optional
from pipelines.common.errors import PostgresError
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = _REPO_ROOT / "warehouse" / "ddl"

_MIGRATION_FILENAME_RE = re.compile(r"^(\d{3,})_.+\.sql$")


class MigrationError(PostgresError):
    """Base class for migration-runner failures. Subclass of
    PostgresError (Task 46) since a migration failure is fundamentally a
    Postgres-schema-state problem."""

    def __init__(self, message: str, *, stage: str = "Postgres Migrations", **kwargs):
        super().__init__(message, stage=stage, **kwargs)

class MigrationChecksumError(MigrationError):
    """Raised when an already-applied migration's file contents have
    changed on disk -- migrations must be append-only. Fix forward with a
    new migration file instead of editing history."""


class Migration(NamedTuple):
    version: str
    filename: str
    path: Path
    checksum: str
    sql: str


def discover_migrations(ddl_dir: Path = DDL_DIR) -> List[Migration]:
    """Find every `NNN_*.sql` file in `ddl_dir`, sorted by numeric prefix.
    The three-digit `version` (e.g. "000", "003") is the sort/tracking
    key -- filenames may otherwise be renamed/reworded freely."""
    migrations: List[Migration] = []
    for path in sorted(ddl_dir.glob("*.sql")):
        match = _MIGRATION_FILENAME_RE.match(path.name)
        if not match:
            continue  # non-migration file sitting in ddl/, ignored
        version = match.group(1)
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(version=version, filename=path.name, path=path, checksum=checksum, sql=sql))

    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        dupes = sorted({v for v in versions if versions.count(v) > 1})
        raise MigrationError(f"Duplicate migration version prefix(es) found: {dupes}")

    return sorted(migrations, key=lambda m: m.version)


def _ensure_migrations_table(conn) -> None:
    """Bootstraps just enough for tracking to work: the `meta` schema and
    meta.schema_migrations itself. Idempotent by construction (IF NOT
    EXISTS everywhere)."""
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS meta;")
        migrations_ddl_path = DDL_DIR / "000_schema_migrations.sql"
        cur.execute(migrations_ddl_path.read_text())


def _applied_versions(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM meta.schema_migrations;")
        return {row[0]: row[1] for row in cur.fetchall()}


def apply_migrations(conn, ddl_dir: Path = DDL_DIR, dry_run: bool = False) -> List[str]:
    """Apply every unapplied migration in `ddl_dir`, in order, each inside
    its own transaction. Safe to call repeatedly (Task 26/27 idempotency
    requirement) -- already-applied migrations are skipped entirely, so
    running this against an already-fully-migrated database is a no-op.

    Returns the list of migration filenames that were newly applied this
    call (empty list if the database was already up to date).
    """
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    migrations = discover_migrations(ddl_dir)

    newly_applied: List[str] = []
    conn.autocommit = False
    try:
        for migration in migrations:
            if migration.version in applied:
                if applied[migration.version] != migration.checksum:
                    raise MigrationChecksumError(
                        f"Migration {migration.filename!r} (version {migration.version}) has changed "
                        f"since it was applied. Migrations are append-only -- add a new migration "
                        f"file instead of editing an applied one."
                    )
                continue  # already applied, checksum matches -- skip

            logger.info("Applying migration %s", migration.filename)
            if dry_run:
                newly_applied.append(migration.filename)
                continue

            with conn.cursor() as cur:
                cur.execute(migration.sql)
                cur.execute(
                    """
                    INSERT INTO meta.schema_migrations (version, filename, checksum)
                    VALUES (%(version)s, %(filename)s, %(checksum)s)
                    ON CONFLICT (version) DO NOTHING;
                    """,
                    {"version": migration.version, "filename": migration.filename, "checksum": migration.checksum},
                )
            conn.commit()
            newly_applied.append(migration.filename)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True

    return newly_applied


def assert_up_to_date(conn, ddl_dir: Path = DDL_DIR) -> None:
    """Raise MigrationError if any migration file hasn't been applied.
    Used as a hard guard before pipeline writes (e.g.
    load_gold_to_postgres.py / load_silver_to_postgres.py) so a missing
    migration fails loudly instead of pandas silently creating a
    constraint-less table -- the exact failure mode this task fixes.
    """
    applied = _applied_versions(conn)
    migrations = discover_migrations(ddl_dir)
    pending = [m.filename for m in migrations if m.version not in applied]
    if pending:
        raise MigrationError(
            f"{len(pending)} migration(s) have not been applied: {pending}. "
            f"Run pipelines.common.migrations.apply_migrations() (or the warehouse "
            f"bootstrap) before writing to the warehouse."
        )