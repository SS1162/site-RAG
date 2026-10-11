"""Fetcher port: downloads one URL politely. Implementations: httpx (Playwright later).

Also holds the small types the stages need from robots.txt and sitemaps, so that stages
depend on this module and not on the HTTP adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from siterag.domain.enums import ContentType


class SkipReason(StrEnum):
    """Why a URL was not downloaded."""

    EXTENSION = "extension"
    CONTENT_TYPE = "content_type"
    TOO_LARGE = "too_large"
    OFFSITE_REDIRECT = "offsite_redirect"
    OUT_OF_SCOPE_REDIRECT = "out_of_scope_redirect"


@dataclass(frozen=True)
class FetchResult:
    """Outcome of fetching one URL (after redirects and retries).

    Exactly one of these describes the outcome:
    ``error`` (network error, or retries exhausted), ``skip_reason``, or ``status``.
    """

    requested_url: str
    final_url: str
    """URL of the last response; differs from ``requested_url`` after same-host redirects."""
    status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    """Headers of the final response (lower-case names)."""
    content: bytes | None = None
    """Body exactly as received (only for successful document responses)."""
    content_type: ContentType | None = None
    skip_reason: SkipReason | None = None
    error: str | None = None
    redirect_status: int | None = None
    """Status of the first redirect response, when redirected."""
    elapsed_s: float = 0.0


@dataclass
class FetchStats:
    """Counters over every HTTP response of a run (all attempts)."""

    requests: int = 0
    not_modified: int = 0
    total_elapsed_s: float = 0.0

    @property
    def avg_response_s(self) -> float:
        """Average response time in seconds (0 when nothing was requested)."""
        return self.total_elapsed_s / self.requests if self.requests else 0.0


class Fetcher(Protocol):
    """Downloads URLs one at a time, honouring rate limits, retries and redirects."""

    stats: FetchStats

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        documents_only: bool = True,
    ) -> FetchResult:
        """Fetch ``url``; send conditional headers when ``etag``/``last_modified`` are given.

        With ``documents_only`` only allowed document types are downloaded (others are
        skipped); without it any type is returned (robots.txt, sitemaps).
        Raises ``CircuitOpenError`` when the server keeps refusing requests.
        """
        ...

    def set_crawl_delay(self, seconds: float | None) -> None:
        """Apply robots.txt ``Crawl-delay`` (the larger of it and the profile delay wins)."""
        ...

    def close(self) -> None:
        """Release network resources."""
        ...


class RobotsRules(Protocol):
    """Parsed robots.txt."""

    @property
    def sitemaps(self) -> list[str]:
        """``Sitemap:`` URLs declared in robots.txt."""
        ...

    @property
    def crawl_delay(self) -> float | None:
        """``Crawl-delay`` for our user agent, if any."""
        ...

    def allowed(self, url: str) -> bool:
        """True when our user agent may fetch ``url``."""
        ...


@dataclass(frozen=True)
class SitemapEntry:
    """One ``<url>`` or ``<sitemap>`` of a sitemap file."""

    loc: str
    lastmod: datetime | None = None


@dataclass(frozen=True)
class Sitemap:
    """A parsed sitemap file: either a ``<urlset>`` or a ``<sitemapindex>``."""

    is_index: bool
    entries: list[SitemapEntry]
