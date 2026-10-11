"""Test doubles for the HTTP layer: fake time and a fetcher over httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from siterag.adapters.http.httpx_fetcher import HttpxFetcher
from siterag.adapters.http.rate_limiter import RateLimiter

Handler = Callable[[httpx.Request], httpx.Response]


class FakeTime:
    """Monotonic clock + sleep: sleeping advances the clock and is recorded."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_fetcher(
    handler: Handler, time: FakeTime | None = None, **overrides: Any
) -> HttpxFetcher:
    """An HttpxFetcher with no real network and no real waiting."""
    time = time or FakeTime()
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": "site-rag-test/0.1"}
    )
    rate = RateLimiter(1.5, 0.5, 2.0, clock=time.clock, sleep=time.sleep, rand=lambda: 0.0)
    options: dict[str, Any] = {
        "allowed_content_types": ["text/html", "application/pdf"],
        "max_bytes": 20 * 1024 * 1024,
        "clock": time.clock,
        "sleep": time.sleep,
    }
    options.update(overrides)
    return HttpxFetcher(client, rate, **options)
