"""Engine and session factory for SQLite, with the required per-connection pragmas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _json_serializer(obj: Any) -> str:
    # Keep Hebrew readable in the database instead of \uXXXX escapes.
    return json.dumps(obj, ensure_ascii=False)


def sqlite_url(db_path: Path) -> str:
    """Return the SQLAlchemy URL of a SQLite file."""
    return f"sqlite:///{db_path.as_posix()}"


def create_sqlite_engine(db_path: Path) -> Engine:
    """Create an engine for ``db_path`` with WAL, foreign keys and a busy timeout.

    pysqlite's own transaction handling is disabled and SQLAlchemy emits BEGIN itself,
    so that SAVEPOINTs (used for atomic chunk replacement) work correctly.
    """
    engine = create_engine(sqlite_url(db_path), json_serializer=_json_serializer)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)
