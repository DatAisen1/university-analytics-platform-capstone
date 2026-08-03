"""
warehouse/models/base.py

Shared declarative base + naming convention. The naming convention
matters here specifically because this project's existing DDL
(warehouse/ddl/*.sql, ported into migrations/versions/) uses explicit,
descriptive constraint names (uq_gold_dim_program_program_id,
pk_silver_college, fk_silver_program_college, ...) rather than
Postgres's auto-generated ones. Every model below sets `name=` explicitly
on its constraints to match those exact names -- so a constraint-
violation error in production (e.g. "duplicate key value violates unique
constraint uq_gold_fact_enrollment_student_period") is traceable straight
back to the ORM model that defines it, with no name mismatch between
what psycopg2 reports and what this codebase calls it.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Fallback convention for any FUTURE constraint added without an explicit
# name -- existing constraints below always pass name= explicitly and are
# unaffected by this; this only prevents "sa_autoincrement"-style default
# names creeping in for anything added later without matching this
# project's explicit style.
_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)