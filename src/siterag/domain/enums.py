"""Enumerations shared across the domain. Stored as plain strings in the database."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Lifecycle of a pipeline run."""

    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class PageStatus(StrEnum):
    """Display/report status of a page. Never used for work selection."""

    DISCOVERED = "discovered"
    FETCHED = "fetched"
    EXTRACTED = "extracted"
    CHUNKED = "chunked"
    INDEXED = "indexed"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"
    REMOVED = "removed"
    REDIRECTED = "redirected"
    DUPLICATE = "duplicate"


class ContentType(StrEnum):
    """Kind of document behind a URL."""

    HTML = "html"
    PDF = "pdf"
    OTHER = "other"


class DiscoveredVia(StrEnum):
    """How a URL was first found."""

    SEED = "seed"
    SITEMAP = "sitemap"
    LINK = "link"


class EventType(StrEnum):
    """Per-page events recorded during a run."""

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    RESTORED = "restored"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"
    REDIRECTED = "redirected"
    DUPLICATE = "duplicate"


INACTIVE_STATUSES: frozenset[PageStatus] = frozenset(
    {
        PageStatus.REMOVED,
        PageStatus.REDIRECTED,
        PageStatus.DUPLICATE,
        PageStatus.FAILED_PERMANENT,
    }
)
"""Pages in these statuses are not active and are never selected for processing."""

UNINDEX_STATUSES: frozenset[PageStatus] = frozenset(
    {PageStatus.REMOVED, PageStatus.REDIRECTED, PageStatus.DUPLICATE}
)
"""Pages in these statuses must be taken out of the index."""
