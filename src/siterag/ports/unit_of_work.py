"""Unit of Work interface: one transaction spanning all repositories."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from siterag.ports.repositories import (
    ChunkRepository,
    EventRepository,
    PageRepository,
    ReceiptRepository,
    RunRepository,
)


class UnitOfWork(Protocol):
    """Transaction boundary. Use as a context manager and call ``commit()`` explicitly.

    Leaving the block without committing - or because of an exception - rolls back.
    """

    runs: RunRepository
    pages: PageRepository
    receipts: ReceiptRepository
    chunks: ChunkRepository
    events: EventRepository

    def __enter__(self) -> Self:
        """Open the transaction and the repositories."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back uncommitted work and release the connection."""
        ...

    def commit(self) -> None:
        """Commit the work done so far."""
        ...

    def rollback(self) -> None:
        """Discard the work done since the last commit."""
        ...
