from pathlib import Path

from sqlalchemy import inspect

from siterag.adapters.sqlalchemy import migrate
from siterag.adapters.sqlalchemy.engine import create_sqlite_engine


def test_upgrade_on_empty_db_matches_models(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "state.db"  # directory is created
    migrate.upgrade(db_path)
    migrate.check(db_path)  # raises if models and schema differ (alembic check)

    engine = create_sqlite_engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    finally:
        engine.dispose()
    assert {"runs", "pages", "stage_receipts", "chunks", "page_events"} <= tables


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    migrate.upgrade(db_path)
    migrate.upgrade(db_path)
    migrate.check(db_path)
