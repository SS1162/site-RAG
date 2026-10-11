"""Repository interfaces. They take and return domain entities only."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any, Protocol

from siterag.domain.entities import Chunk, Page, PageEvent, Run, StageReceipt
from siterag.domain.enums import DiscoveredVia, EventType, PageStatus, RunStatus
from siterag.domain.urls import NormalizedUrl


class RunRepository(Protocol):
    """Pipeline runs and the run lock."""

    def start(
        self,
        site_id: str,
        command: str,
        args: dict[str, Any],
        config_hash: str,
        site_profile_hash: str,
        stale_after_hours: float = 12,
    ) -> Run:
        """Create a ``running`` run.

        Raises ``RunLockedError`` if another run started less than ``stale_after_hours`` ago
        is still running; an older one is marked ``aborted`` with reason ``stale``.
        """
        ...

    def finish(self, run_id: str, status: RunStatus, abort_reason: str | None = None) -> Run:
        """Set the final status and ``finished_at`` of a run."""
        ...

    def increment(self, run_id: str, **counters: int) -> None:
        """Add the given amounts to the run's page counters (e.g. ``pages_new=1``)."""
        ...

    def get(self, run_id: str) -> Run | None:
        """Return a run by id, or None."""
        ...

    def get_last(self) -> Run | None:
        """Return the most recently started run, or None."""
        ...


class PageRepository(Protocol):
    """Pages of the site and receipt-based work selection."""

    def get(self, url_id: str) -> Page | None:
        """Return a page by id, or None."""
        ...

    def get_by_url(self, url: str) -> Page | None:
        """Return a page by its normalized URL, or None."""
        ...

    def upsert_discovered(
        self,
        normalized_url: NormalizedUrl,
        discovered_via: DiscoveredVia,
        found_on: str | None,
        sitemap_lastmod: datetime | None,
        run_id: str,
    ) -> tuple[Page, bool]:
        """Insert a newly discovered page or refresh ``last_seen_*`` of an existing one.

        A ``removed`` page is restored (status ``discovered``, event ``restored``).
        Returns the page and whether it is new.
        """
        ...

    def update(self, page: Page) -> None:
        """Persist all fields of an existing page."""
        ...

    def mark_removed(self, url_id: str, run_id: str) -> Page:
        """Mark a page removed (verified 404/410) and record an event."""
        ...

    def mark_redirected(self, url_id: str, target_url_id: str, run_id: str) -> Page:
        """Mark a page as redirecting to another page and record an event."""
        ...

    def mark_duplicate(self, url_id: str, of_url_id: str, run_id: str) -> Page:
        """Mark a page as a duplicate of another page and record an event."""
        ...

    def list_removal_candidates(self, run_id: str) -> list[Page]:
        """Active pages that were not seen in the given run."""
        ...

    def iter_needing_stage(
        self, stage: str, stage_version: str, limit: int | None = None
    ) -> Iterator[Page]:
        """Active pages whose receipt for ``stage`` is missing or out of date."""
        ...

    def list_pending_unindex(self) -> list[Page]:
        """Removed/redirected/duplicate pages that still have an ``index`` receipt."""
        ...

    def reset_failures_for_version_change(
        self, stage: str, stage_version: str, run_id: str
    ) -> list[Page]:
        """Make pages that failed in ``stage`` under an older version eligible again."""
        ...

    def count_by_status(self) -> dict[PageStatus, int]:
        """Number of pages per status."""
        ...

    def count_active(self) -> int:
        """Number of active pages."""
        ...


class ReceiptRepository(Protocol):
    """Stage receipts: "stage X processed input H using logic version V"."""

    def get(self, url_id: str, stage: str) -> StageReceipt | None:
        """Return the receipt of a page for a stage, or None."""
        ...

    def upsert(
        self, url_id: str, stage: str, input_hash: str, stage_version: str, run_id: str
    ) -> StageReceipt:
        """Create or replace the receipt of a page for a stage."""
        ...

    def delete_for_page(self, url_id: str, stage: str | None = None) -> int:
        """Delete one stage's receipt (or all receipts) of a page; return the count."""
        ...


class ChunkRepository(Protocol):
    """Chunks; state.db is the source of truth for chunk text."""

    def replace_for_page(self, url_id: str, chunks: Sequence[Chunk]) -> None:
        """Atomically replace all chunks of a page with ``chunks``."""
        ...

    def list_by_page(self, url_id: str) -> list[Chunk]:
        """Chunks of a page ordered by ordinal."""
        ...

    def iter_all(self, batch_size: int = 1000) -> Iterator[Chunk]:
        """All chunks, loaded ``batch_size`` rows at a time."""
        ...

    def delete_for_page(self, url_id: str) -> int:
        """Delete all chunks of a page; return the count."""
        ...

    def count(self) -> int:
        """Total number of chunks."""
        ...


class EventRepository(Protocol):
    """Per-page event log."""

    def add(
        self,
        run_id: str,
        url_id: str,
        event: EventType,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> PageEvent:
        """Record an event."""
        ...

    def summary(self, run_id: str) -> dict[EventType, int]:
        """Number of events per type in a run."""
        ...

    def list(self, run_id: str, event: EventType | None = None) -> Sequence[PageEvent]:
        """Events of a run, optionally filtered by type, in insertion order."""
        ...
