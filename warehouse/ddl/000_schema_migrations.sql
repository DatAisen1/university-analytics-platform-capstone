-- warehouse/ddl/000_schema_migrations.sql
--
-- Tracks which migration files have already been applied, so the
-- migration runner (pipelines/common/migrations.py) can safely re-run
-- against a database that's already partially or fully migrated.
-- Must be file #000 -- every other migration depends on this table
-- existing first, including 001, which previously ran unconditionally
-- and un-tracked.

CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    version      VARCHAR(32)  PRIMARY KEY,
    filename     VARCHAR(255) NOT NULL,
    checksum     VARCHAR(64)  NOT NULL,
    applied_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);