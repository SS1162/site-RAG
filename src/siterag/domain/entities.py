"""Domain entities: plain dataclasses, independent of any persistence technology."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from siterag.domain.enums import (
    INACTIVE_STATUSES,
    ContentType,
    DiscoveredVia,
    EventType,
    PageStatus,
    RunStatus,
)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


RUN_COUNTERS: tuple[str, ...] = (
    "pages_new",
    "pages_changed",
    "pages_unchanged",
    "pages_removed",
    "pages_failed",
    "pages_redirected",
    "pages_skipped",
    "pages_orphaned",
)
"""Names of the per-run page counters."""


@dataclass
class Run:
    """One execution of a pipeline command for a site."""

    run_id: str
    site_id: str
    status: RunStatus
    started_at: datetime
    command: str
    args: dict[str, Any] = field(default_factory=dict)
    config_hash: str = ""
    site_profile_hash: str = ""
    finished_at: datetime | None = None
    pages_new: int = 0
    pages_changed: int = 0
    pages_unchanged: int = 0
    pages_removed: int = 0
    pages_failed: int = 0
    pages_redirected: int = 0
    pages_skipped: int = 0
    pages_orphaned: int = 0
    abort_reason: str | None = None


@dataclass
class Page:
    """A URL of the site and its position in the pipeline."""

    url_id: str
    url: str
    url_display: str
    status: PageStatus
    discovered_via: DiscoveredVia
    first_seen_at: datetime
    last_seen_at: datetime
    last_seen_run_id: str
    canonical_url: str | None = None
    duplicate_of: str | None = None
    redirected_to: str | None = None
    content_type: ContentType | None = None
    found_on: str | None = None
    discovery_depth: int | None = None
    sitemap_lastmod: datetime | None = None
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    raw_hash: str | None = None
    clean_hash: str | None = None
    chunks_hash: str | None = None
    title: str | None = None
    domain: str | None = None
    page_type: str | None = None
    language: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    last_fetch_ok_at: datetime | None = None
    last_fetch_ok_run_id: str | None = None
    removed_at: datetime | None = None
    fail_count: int = 0
    failed_stage: str | None = None
    failed_stage_version: str | None = None
    last_failed_run_id: str | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """True when the page takes part in processing."""
        return self.status not in INACTIVE_STATUSES


@dataclass(frozen=True)
class StageReceipt:
    """Proof that a stage processed a given input with a given logic version."""

    url_id: str
    stage: str
    input_hash: str
    stage_version: str
    run_id: str
    processed_at: datetime


@dataclass
class Chunk:
    """A retrievable piece of a page's text. state.db is the source of truth for it."""

    chunk_id: str
    url_id: str
    ordinal: int
    heading_path: list[str]
    text: str
    token_count: int
    chunker_version: str
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class PageEvent:
    """Something that happened to a page during a run."""

    id: int
    run_id: str
    url_id: str
    event: EventType
    stage: str | None
    details: dict[str, Any]
    created_at: datetime
