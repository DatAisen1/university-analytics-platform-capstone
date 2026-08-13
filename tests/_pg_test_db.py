"""Shared helper for tests that need a genuinely private, throwaway
Postgres database -- as opposed to tests that intentionally validate a
real, already-populated warehouse (see module docstring in
tests/unit/test_dbt_marts.py for that other category).

Any test module that does something destructive -- DROP SCHEMA, drop
roles, rebuild from a blank database -- must use `create_isolated_database`
here rather than operating on `TEST_ENV["POSTGRES_DB"]` directly. Sharing
one mutable database name across multiple destructive test modules is
exactly what caused the original P0.54-style corruption bug (and, later,
a second-order version of it: `test_dbt_marts.py` / `test_train_prophet.py`
expect real Bronze->Silver->Gold->dbt data to already exist in
`TEST_POSTGRES_DB`, so any *other* module that DROPs schemas in that same
database destroys the data those tests need -- an architecture-level
collision between two categories of tests, not a single typo).
"""
from __future__ import annotations

import uuid
from typing import Dict

import psycopg2


def _fresh_maintenance_connection(env: Dict[str, str]):
    """A brand-new connection to Postgres's `postgres` maintenance
    database, with autocommit forced via BOTH the `.autocommit` property
    AND `set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)` -- belt and
    suspenders, since CREATE DATABASE / DROP DATABASE must never run
    inside any transaction block, explicit or implicit."""
    conn = psycopg2.connect(
        host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"], dbname="postgres",
        user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"],
    )
    conn.autocommit = True
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def drop_database_if_exists(db_name: str, env: Dict[str, str]) -> None:
    """Terminate any lingering sessions against `db_name` (a prior
    interrupted test run can leave one open -- DROP DATABASE refuses to
    run while any session is attached) then drop it.

    Deliberately uses a SEPARATE, single-purpose connection for the
    terminate step and another for the drop step -- each connection runs
    exactly one statement and is then closed. This is not merely "extra
    caution": running two DDL/admin statements back-to-back on one
    connection, even with autocommit set, is exactly the pattern that
    previously produced an unexplained "DROP DATABASE cannot run inside a
    transaction block" error. Whatever the precise cause (a subtlety in
    how autocommit mode interacts with a connection that already ran a
    prior statement), giving each statement its own fresh connection
    removes the possibility entirely -- there is no prior statement left
    for any transaction state to carry over from.
    """
    conn = _fresh_maintenance_connection(env)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
    finally:
        conn.close()

    conn = _fresh_maintenance_connection(env)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        conn.close()


def create_isolated_database(base_name: str, env: Dict[str, str]) -> str:
    """Creates a brand-new database named `{base_name}_{random suffix}`
    and returns its name. Callers are responsible for calling
    `drop_database_if_exists` on the returned name during teardown.

    Using a random suffix (not a fixed name) means two test runs can
    never collide even if a previous run's teardown didn't get to run
    (e.g. a killed process) -- the old database is simply orphaned under
    its own unique name rather than blocking or corrupting the new run.
    """
    db_name = f"{base_name}_{uuid.uuid4().hex[:8]}"
    drop_database_if_exists(db_name, env)  # defensive: extremely unlikely collision, but free to guard against

    conn = _fresh_maintenance_connection(env)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        conn.close()

    return db_name