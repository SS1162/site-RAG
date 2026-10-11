"""Migration 0002 upgrades a Task 01 (revision 0001) database and keeps its data."""

from pathlib import Path

from siterag.adapters.sqlalchemy import migrate
from siterag.adapters.sqlalchemy.engine import create_sqlite_engine

RUN = (
    "INSERT INTO runs (run_id, site_id, status, started_at, command, args, config_hash, "
    "site_profile_hash, pages_new) VALUES ('r1', 'fnx', 'completed', "
    "'2026-10-11T10:00:00.000000+00:00', 'run', '{}', 'c', 's', 7)"
)
PAGE = (
    "INSERT INTO pages (url_id, url, url_display, status, discovered_via, extra, "
    "first_seen_at, last_seen_at, last_seen_run_id, raw_hash) VALUES ('p1', "
    "'https://x.co.il/a/', 'https://x.co.il/a/', 'indexed', 'sitemap', '{}', "
    "'2026-10-11T10:00:00.000000+00:00', '2026-10-11T10:00:00.000000+00:00', 'r1', 'h')"
)
EVENT = (
    "INSERT INTO page_events (run_id, url_id, event, details, created_at) VALUES "
    "('r1', 'p1', 'new', '{}', '2026-10-11T10:00:00.000000+00:00')"
)


def test_upgrade_from_0001_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    migrate.upgrade(db_path, "0001")
    engine = create_sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            for sql in (RUN, PAGE, EVENT):
                conn.exec_driver_sql(sql)
    finally:
        engine.dispose()

    migrate.upgrade(db_path)
    migrate.check(db_path)

    engine = create_sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            page = conn.exec_driver_sql(
                "SELECT status, raw_hash, discovery_depth FROM pages WHERE url_id='p1'"
            ).one()
            run = conn.exec_driver_sql(
                "SELECT pages_new, pages_skipped, pages_orphaned FROM runs WHERE run_id='r1'"
            ).one()
            events = conn.exec_driver_sql("SELECT count(*) FROM page_events").scalar()
            # The new enum values can be stored (no CHECK constraint rejects them).
            conn.exec_driver_sql("UPDATE pages SET status='skipped' WHERE url_id='p1'")
            conn.exec_driver_sql(
                "INSERT INTO page_events (run_id, url_id, event, details, created_at) VALUES "
                "('r1', 'p1', 'orphan', '{}', '2026-10-11T10:00:00.000000+00:00')"
            )
    finally:
        engine.dispose()
    assert tuple(page) == ("indexed", "h", None)
    assert tuple(run) == (7, 0, 0)
    assert events == 1


def test_downgrade_to_0001_and_back(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    migrate.upgrade(db_path)
    migrate.downgrade(db_path, "0001")
    migrate.upgrade(db_path)
    migrate.check(db_path)
