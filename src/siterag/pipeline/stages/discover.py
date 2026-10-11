"""Discover stage: robots.txt, seeds and sitemaps → pages at depth 0.

Link-following happens in the fetch stage, through the same ``Frontier``.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

from siterag.domain.enums import DiscoveredVia
from siterag.domain.safety import check_discovery
from siterag.pipeline.context import RunContext
from siterag.ports.fetcher import Fetcher, RobotsRules, Sitemap
from siterag.ports.stage import StageResult
from siterag.ports.unit_of_work import UnitOfWork

MAX_SITEMAP_NESTING = 3
"""How deep ``<sitemapindex>`` files may nest."""


def load_robots_into(ctx: RunContext, fetcher: Fetcher, load: Callable[[], RobotsRules]) -> None:
    """Fetch robots.txt for this run (once) and apply its Crawl-delay."""
    if ctx.frontier.robots is None:
        ctx.frontier.robots = load()
        fetcher.set_crawl_delay(ctx.frontier.robots.crawl_delay)


class DiscoverStage:
    """Seeds the run with the site root, the profile seeds and every sitemap URL."""

    name = "discover"

    def __init__(
        self,
        fetcher: Fetcher,
        robots_loader: Callable[[], RobotsRules],
        sitemap_parser: Callable[[bytes], Sitemap],
    ) -> None:
        self._fetcher = fetcher
        self._robots_loader = robots_loader
        self._parse_sitemap = sitemap_parser

    def run(self, ctx: RunContext) -> StageResult:
        """Discover in one transaction; raise ``DiscoveryTooSmallError`` if too few URLs."""
        with ctx.uow_factory() as uow:
            self.discover(ctx, uow)
            uow.commit()
        stats = ctx.frontier.stats
        return StageResult(self.name, {"discovered": stats.discovered})

    def discover(self, ctx: RunContext, uow: UnitOfWork) -> None:
        """Do discovery inside ``uow`` without committing (also used by ``--dry-run``)."""
        load_robots_into(ctx, self._fetcher, self._robots_loader)
        frontier = ctx.frontier
        assert frontier.robots is not None

        root = urljoin(ctx.site.base_url, "/")
        for seed in [root, *ctx.site.seeds]:
            frontier.admit(uow, seed, DiscoveredVia.SEED, None, 0)

        sitemaps = frontier.robots.sitemaps or ctx.site.sitemaps
        if not sitemaps:
            ctx.warn("No sitemap declared in robots.txt or in the site profile")
        visited: set[str] = set()
        for url in sitemaps:
            self._walk(ctx, uow, url, 0, visited)

        ctx.logger.info("Discovered %d URLs", frontier.stats.discovered)
        check_discovery(frontier.stats.discovered, ctx.site.safety.discovery_min_pages)

    def _walk(
        self, ctx: RunContext, uow: UnitOfWork, url: str, nesting: int, visited: set[str]
    ) -> None:
        if url in visited:
            return
        visited.add(url)
        if urlsplit(url).hostname != urlsplit(ctx.site.base_url).hostname:
            ctx.warn(f"Sitemap {url} is on another host: ignored")
            return
        result = self._fetcher.fetch(url, documents_only=False)
        if result.status != 200 or result.content is None:
            detail = result.error or result.skip_reason or f"HTTP {result.status}"
            ctx.warn(f"Sitemap {url} could not be read ({detail}): skipped")
            return
        try:
            sitemap = self._parse_sitemap(result.content)
        except ValueError as exc:
            ctx.warn(f"Sitemap {url} is invalid ({exc}): skipped")
            return

        if sitemap.is_index:
            if nesting >= MAX_SITEMAP_NESTING:
                ctx.warn(f"Sitemap index {url} nested too deep: skipped")
                return
            for entry in sitemap.entries:
                self._walk(ctx, uow, entry.loc, nesting + 1, visited)
            return
        ctx.logger.info("Sitemap %s: %d URLs", url, len(sitemap.entries))
        for entry in sitemap.entries:
            ctx.frontier.admit(
                uow, entry.loc, DiscoveredVia.SITEMAP, None, 0, lastmod=entry.lastmod
            )
