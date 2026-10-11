"""Shared fixtures: a migrated temporary state.db per test and a controllable clock."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from siterag.adapters.sqlalchemy import migrate
from siterag.adapters.sqlalchemy.engine import create_session_factory, create_sqlite_engine
from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.domain.entities import Page, Run
from siterag.domain.enums import DiscoveredVia
from siterag.domain.urls import UrlNormalizationRules, normalize_url

STAGE_INPUTS = {"extract": "raw_hash", "chunk": "clean_hash", "index": "chunks_hash"}
RULES = UrlNormalizationRules()


class FakeClock:
    """Clock whose time only moves when the test says so."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database migrated once per session; each test gets a copy."""
    path = tmp_path_factory.mktemp("template") / "state.db"
    migrate.upgrade(path)
    return path


@pytest.fixture
def db_path(tmp_path: Path, migrated_template: Path) -> Path:
    path = tmp_path / "state.db"
    shutil.copy(migrated_template, path)
    return path


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    engine = create_sqlite_engine(db_path)
    yield engine
    engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


UowFactory = Callable[[], SqlAlchemyUnitOfWork]


@pytest.fixture
def make_uow(engine: Engine, clock: FakeClock) -> UowFactory:
    factory = create_session_factory(engine)
    return lambda: SqlAlchemyUnitOfWork(factory, STAGE_INPUTS, clock)


def start_run(uow: SqlAlchemyUnitOfWork) -> Run:
    """Start a run with dummy hashes (caller commits)."""
    return uow.runs.start("test", "run", {}, "cfg", "site")


def discover(uow: SqlAlchemyUnitOfWork, path: str, run_id: str) -> Page:
    """Discover ``https://x.co.il<path>`` and return the page."""
    page, _ = uow.pages.upsert_discovered(
        normalize_url(f"https://x.co.il{path}", RULES), DiscoveredVia.LINK, None, None, run_id
    )
    return page
