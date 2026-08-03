"""
warehouse/models

SQLAlchemy ORM declarative models mirroring the Postgres schema defined
in Alembic migrations (migrations/versions/). This package is a TYPED
QUERY/CONTRACT layer, not the source of schema truth -- the migrations
remain the source of truth (see migrations/env.py's module docstring for
why autogenerate-from-models is deliberately not used for DDL).

Import Base from here when adding new models; import concrete model
classes from .silver / .gold directly.
"""

from warehouse.models.base import Base

__all__ = ["Base"]