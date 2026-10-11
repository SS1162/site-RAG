"""SQLAlchemy Unit of Work: one session/transaction shared by all repositories."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from siterag.adapters.sqlalchemy.repositories import (
    Clock,
    SqlAlchemyChunkRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyPageRepository,
    SqlAlchemyReceiptRepository,
    SqlAlchemyRunRepository,
    validate_stage_inputs,
)
from siterag.domain.entities import utc_now


class SqlAlchemyUnitOfWork:
    """Unit of Work over a SQLAlchemy session.

    Each ``with`` block opens a new session. Work must be committed explicitly;
    anything uncommitted when the block exits (normally or by exception) is rolled back.
    """

    runs: SqlAlchemyRunRepository
    pages: SqlAlchemyPageRepository
    receipts: SqlAlchemyReceiptRepository
    chunks: SqlAlchemyChunkRepository
    events: SqlAlchemyEventRepository

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        stage_inputs: Mapping[str, str],
        clock: Clock = utc_now,
    ) -> None:
        validate_stage_inputs(stage_inputs)
        self._session_factory = session_factory
        self._stage_inputs = dict(stage_inputs)
        self._clock = clock
        self._session: Session | None = None

    def __enter__(self) -> Self:
        """Open a session and the repositories bound to it."""
        session = self._session_factory()
        self._session = session
        self.runs = SqlAlchemyRunRepository(session, self._clock)
        self.pages = SqlAlchemyPageRepository(session, self._clock, self._stage_inputs)
        self.receipts = SqlAlchemyReceiptRepository(session, self._clock)
        self.chunks = SqlAlchemyChunkRepository(session)
        self.events = SqlAlchemyEventRepository(session, self._clock)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back anything uncommitted and close the session."""
        session = self._require_session()
        try:
            session.rollback()
        finally:
            session.close()
            self._session = None

    def commit(self) -> None:
        """Commit the current transaction."""
        self._require_session().commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of Work used outside a 'with' block")
        return self._session
