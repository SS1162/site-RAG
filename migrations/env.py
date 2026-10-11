"""Alembic environment for state.db.

The database path comes from ``config.attributes["db_path"]`` (set by the CLI) or from
``alembic -x db=data/<site_id>/state.db ...`` when Alembic is run by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import context

from siterag.adapters.sqlalchemy.engine import create_sqlite_engine
from siterag.adapters.sqlalchemy.models import Base
from siterag.adapters.sqlalchemy.types import UTCDateTime

config = context.config
target_metadata = Base.metadata


def _db_path() -> Path:
    db_path = config.attributes.get("db_path") or context.get_x_argument(as_dictionary=True).get(
        "db"
    )
    if not db_path:
        raise RuntimeError("No database given: use 'alembic -x db=data/<site_id>/state.db ...'")
    return Path(db_path)


def _render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render custom types as plain SQLAlchemy types so migrations stay self-contained."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.String(length=32)"
    return False


def run_migrations_online() -> None:
    """Run migrations with a live connection (the only mode supported)."""
    engine = create_sqlite_engine(_db_path())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                render_item=_render_item,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported")
run_migrations_online()
