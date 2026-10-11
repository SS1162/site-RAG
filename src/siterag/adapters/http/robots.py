"""robots.txt via protego (supports ``*`` and ``$`` wildcards, unlike the stdlib parser)."""

from __future__ import annotations

from urllib.parse import urljoin

from protego import Protego

from siterag.domain.errors import RobotsUnavailableError
from siterag.ports.fetcher import Fetcher

ALLOW_ALL_STATUSES = frozenset({404, 410})
"""robots.txt responses meaning "no rules": everything is allowed."""


class ProtegoRobots:
    """``RobotsRules`` backed by protego. ``parser=None`` allows everything."""

    def __init__(self, parser: Protego | None, user_agent: str) -> None:
        self._parser = parser
        self._user_agent = user_agent

    @classmethod
    def parse(cls, text: str, user_agent: str) -> ProtegoRobots:
        """Parse robots.txt content for ``user_agent`` (the product token, e.g. ``site-rag``)."""
        return cls(Protego.parse(text), user_agent)

    @classmethod
    def allow_all(cls, user_agent: str) -> ProtegoRobots:
        """Rules for a site without robots.txt."""
        return cls(None, user_agent)

    @property
    def sitemaps(self) -> list[str]:
        """``Sitemap:`` URLs declared in robots.txt."""
        return list(self._parser.sitemaps) if self._parser else []

    @property
    def crawl_delay(self) -> float | None:
        """``Crawl-delay`` for our user agent, if any."""
        if self._parser is None:
            return None
        delay = self._parser.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None

    def allowed(self, url: str) -> bool:
        """True when our user agent may fetch ``url``."""
        return self._parser is None or bool(self._parser.can_fetch(url, self._user_agent))


def load_robots(fetcher: Fetcher, base_url: str, user_agent: str) -> ProtegoRobots:
    """Fetch and parse ``<base_url>/robots.txt``.

    404/410 means no rules (allow all). Any other non-200 outcome, including network
    errors and 5xx after retries, raises ``RobotsUnavailableError``.
    """
    url = urljoin(base_url, "/robots.txt")
    result = fetcher.fetch(url, documents_only=False)
    if result.status in ALLOW_ALL_STATUSES:
        return ProtegoRobots.allow_all(user_agent)
    if result.status != 200 or result.content is None:
        detail = result.error or result.skip_reason or f"HTTP {result.status}"
        raise RobotsUnavailableError(f"Cannot read {url}: {detail}")
    text = result.content.decode("utf-8", errors="replace")
    return ProtegoRobots.parse(text, user_agent)
