"""Test doubles for the HTTP layer: fake time and a fetcher over httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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


BASE = "https://www.x.co.il"


def html_page(*links: str, title: str = "") -> bytes:
    """A small HTML page linking to ``links``."""
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return f"<!doctype html><html><body><h1>{title}</h1>{anchors}</body></html>".encode()


@dataclass
class Route:
    """What the fake server answers for one path."""

    status: int = 200
    body: bytes = b""
    content_type: str | None = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


class FakeSite:
    """An in-memory website for httpx.MockTransport, with ETag support.

    Default pages (depths when crawled from the root):
      /robots.txt, /sitemap.xml (/, /a/info/, /b/, /old/, /gone/)
      /           -> /a/, /hidden/ (link only), /photo.jpg, /x/?page=2 (robots), mailto
      /a/         -> /a/deep1/, /files/doc.pdf
      /a/deep1/   -> /a/deep1/deep2/ (depth 3: not enqueued), /files/deep.pdf (enqueued)
      /old/       -> 301 /new/
      /gone/      -> 404
      /orphan/    -> 200 but not linked from anywhere
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Route] = {
            "/robots.txt": Route(
                body=f"User-agent: *\nDisallow: /*?page=\nSitemap: {BASE}/sitemap.xml\n".encode(),
                content_type="text/plain",
            ),
            "/sitemap.xml": Route(body=self.sitemap("/", "/a/info/", "/b/", "/old/", "/gone/"),
                                  content_type="application/xml"),
            "/": Route(body=html_page("/a/", "/hidden/", "/photo.jpg", "/x/?page=2",
                                      "mailto:a@x.co.il", "https://other.co.il/")),
            "/a/": Route(body=html_page("/a/deep1/", "/files/doc.pdf")),
            "/a/deep1/": Route(body=html_page("/a/deep1/deep2/", "/files/deep.pdf")),
            "/a/deep1/deep2/": Route(body=html_page()),
            "/a/info/": Route(body=html_page(title="sitemap only")),
            "/b/": Route(body=html_page(title="b")),
            "/hidden/": Route(body=html_page(title="hidden")),
            "/old/": Route(status=301, headers={"Location": "/new/"}, content_type=None),
            "/new/": Route(body=html_page(title="new")),
            "/files/doc.pdf": Route(body=b"%PDF-1.4 doc", content_type="application/pdf"),
            "/files/deep.pdf": Route(body=b"%PDF-1.4 deep", content_type="application/pdf"),
            "/photo.jpg": Route(body=b"\xff\xd8", content_type="image/jpeg"),
            "/orphan/": Route(body=html_page(title="orphan")),
        }

    @staticmethod
    def sitemap(*paths: str) -> bytes:
        urls = "".join(f"<url><loc>{BASE}{p}</loc><lastmod>2026-09-01</lastmod></url>"
                       for p in paths)
        return (f'<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>').encode()

    def paths_requested(self) -> list[str]:
        return [_path(r) for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(_path(request))
        if route is None:
            return httpx.Response(404, text="not found")
        headers = dict(route.headers)
        if route.content_type:
            headers["Content-Type"] = route.content_type
        if route.status == 200:
            etag = f'"{hash(route.body) & 0xFFFFFFFF:x}"'
            headers["ETag"] = etag
            if request.headers.get("If-None-Match") == etag:
                return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(route.status, content=route.body, headers=headers)


def _path(request: httpx.Request) -> str:
    query = request.url.query.decode()
    return request.url.path + (f"?{query}" if query else "")
