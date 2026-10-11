"""The crawl frontier of one run: which URLs are admitted, at what depth, and the queue.

Every URL - seed, sitemap entry, link or redirect target - goes through ``admit``:
normalize → same host → robots.txt → scope rules → depth → ``max_pages`` cap →
classify → upsert. Admitted pages are queued for fetching once per run.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from urllib.parse import urlsplit

from siterag.domain.classify import ClassificationRules, classify
from siterag.domain.entities import Page
from siterag.domain.enums import DiscoveredVia, PageStatus
from siterag.domain.scope import ScopeRules, decoded_path, in_scope
from siterag.domain.urls import NormalizedUrl, UrlNormalizationRules, normalize_url
from siterag.ports.fetcher import RobotsRules
from siterag.ports.unit_of_work import UnitOfWork

ROBOTS_REASON = "robots.txt"
OFFSITE_REASON = "other host"


@dataclass
class DiscoveryStats:
    """What admission saw during the run (shown by ``--dry-run`` and in the report)."""

    discovered: int = 0
    excluded_by_rule: Counter[str] = field(default_factory=Counter)
    robots_blocked: int = 0
    offsite: int = 0
    too_deep: int = 0
    invalid: int = 0
    over_cap: int = 0
    page_types: Counter[str] = field(default_factory=Counter)
    domains: Counter[str] = field(default_factory=Counter)


class Frontier:
    """Admission rules, minimum depths seen in this run, and the fetch queue."""

    def __init__(
        self,
        *,
        run_id: str,
        site_host: str,
        url_rules: UrlNormalizationRules,
        scope_rules: ScopeRules,
        classification: ClassificationRules,
        max_link_depth: int,
        max_pages: int,
        warn: Callable[[str], None],
    ) -> None:
        self.run_id = run_id
        self.site_host = site_host
        self.url_rules = url_rules
        self._scope = scope_rules
        self._classes = classification
        self._max_depth = max_link_depth
        self._max_pages = max_pages
        self._warn = warn
        self.robots: RobotsRules | None = None
        self.depths: dict[str, int] = {}
        """Minimum depth of each URL admitted in this run."""
        self._queue: deque[str] = deque()
        self._done: set[str] = set()
        self.cap_reached = False
        self.stats = DiscoveryStats()

    # -- rules ---------------------------------------------------------------------

    def exclusion(self, url: str) -> str | None:
        """Why a normalized URL is out of scope (other host, robots, rule), or None."""
        if urlsplit(url).hostname != self.site_host:
            return OFFSITE_REASON
        if self.robots is not None and not self.robots.allowed(url):
            return ROBOTS_REASON
        _, rule = in_scope(decoded_path(url), self._scope)
        return rule

    def _count_exclusion(self, reason: str) -> None:
        if reason == OFFSITE_REASON:
            self.stats.offsite += 1
        elif reason == ROBOTS_REASON:
            self.stats.robots_blocked += 1
        else:
            self.stats.excluded_by_rule[reason] += 1

    # -- admission -----------------------------------------------------------------

    def admit(
        self,
        uow: UnitOfWork,
        url: str | NormalizedUrl,
        via: DiscoveredVia,
        found_on: str | None,
        depth: int,
        *,
        is_pdf: bool = False,
        lastmod: datetime | None = None,
        force: bool = False,
    ) -> Page | None:
        """Admit a URL found at ``depth``; return its page, or None when not admitted.

        HTML pages deeper than ``max_link_depth`` are dropped; PDFs are admitted at any
        depth. ``force`` (redirect targets) bypasses the depth limit and the cap, but
        not scope or robots.
        """
        if isinstance(url, str):
            try:
                url = normalize_url(url, self.url_rules)
            except ValueError:
                self.stats.invalid += 1
                return None
        known = self.depths.get(url.url_id)
        if known is not None and known <= depth and lastmod is None:
            return None  # already admitted in this run at this depth or less
        reason = self.exclusion(url.url)
        if reason is not None:
            self._count_exclusion(reason)
            return None
        if not (is_pdf or force) and depth > self._max_depth:
            self.stats.too_deep += 1
            return None
        if known is None and not force and len(self.depths) >= self._max_pages:
            if not self.cap_reached:
                self.cap_reached = True
                self._warn(f"max_pages ({self._max_pages}) reached: no new URLs were enqueued")
            self.stats.over_cap += 1
            return None

        page, _ = uow.pages.upsert_discovered(url, via, found_on, lastmod, self.run_id)
        new_depth = depth if known is None else min(known, depth)
        page_type, domain = classify(decoded_path(url.url), self._classes)
        if (page.discovery_depth, page.page_type, page.domain) != (new_depth, page_type, domain):
            page = replace(page, discovery_depth=new_depth, page_type=page_type, domain=domain)
            uow.pages.update(page)
        self.depths[url.url_id] = new_depth
        if known is None:
            self.stats.discovered += 1
            self.stats.page_types[page_type] += 1
            self.stats.domains[domain] += 1
            self.enqueue(page)
        return page

    # -- queue ---------------------------------------------------------------------

    def enqueue(self, page: Page) -> None:
        """Queue a page for fetching (``failed_permanent`` pages are never fetched)."""
        if page.status != PageStatus.FAILED_PERMANENT and page.url_id not in self._done:
            self._queue.append(page.url_id)

    def next(self) -> str | None:
        """Next url_id to fetch, or None when the queue is empty."""
        while self._queue:
            url_id = self._queue.popleft()
            if url_id not in self._done:
                return url_id
        return None

    def mark_done(self, url_id: str) -> None:
        """Record that a page was handled in this run (it will not be fetched again)."""
        self._done.add(url_id)

    def is_done(self, url_id: str) -> bool:
        """True when the page was already handled in this run."""
        return url_id in self._done

    def depth_of(self, page: Page) -> int:
        """Depth of a page in this run (its stored depth, or 0, if not admitted)."""
        return self.depths.get(page.url_id, page.discovery_depth or 0)


def site_host(base_url: str, rules: UrlNormalizationRules) -> str:
    """Host of the site after normalization (e.g. ``www.fnx.co.il``)."""
    return urlsplit(normalize_url(base_url, rules).url).hostname or ""

