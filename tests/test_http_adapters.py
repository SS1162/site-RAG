"""robots.txt, sitemaps, links, raw store and rate limiter."""

import gzip
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from siterag.adapters.http.rate_limiter import RateLimiter
from siterag.adapters.http.robots import ProtegoRobots, load_robots
from siterag.adapters.http.sitemap import SitemapError, parse_lastmod, parse_sitemap
from siterag.domain.enums import ContentType
from siterag.domain.errors import RobotsUnavailableError
from siterag.domain.urls import TrailingSlash, UrlNormalizationRules
from siterag.ingest.links import extract_links
from siterag.storage.raw_store import RawStore
from tests.fakes import FakeTime, make_fetcher

UA = "site-rag-hackathon"
ROBOTS = (
    "User-agent: *\nDisallow: /*?page=\nDisallow: /private/\nCrawl-delay: 4\n"
    "Sitemap: https://x.co.il/sitemap.xml\n"
)


# -- robots.txt -------------------------------------------------------------------


def test_robots_wildcards_and_crawl_delay() -> None:
    robots = ProtegoRobots.parse(ROBOTS, UA)
    assert not robots.allowed("https://x.co.il/x/?page=2")
    assert not robots.allowed("https://x.co.il/private/a/")
    assert robots.allowed("https://x.co.il/x/")
    assert robots.crawl_delay == 4.0
    assert robots.sitemaps == ["https://x.co.il/sitemap.xml"]


def _robots_status(status: int) -> httpx.Response:
    return httpx.Response(status, text="" if status != 200 else ROBOTS)


def test_robots_404_allows_all() -> None:
    fetcher = make_fetcher(lambda r: _robots_status(404))
    robots = load_robots(fetcher, "https://x.co.il", UA)
    assert robots.allowed("https://x.co.il/x/?page=2") and robots.sitemaps == []


@pytest.mark.parametrize("status", [500, 403])
def test_robots_unavailable(status: int) -> None:
    fetcher = make_fetcher(lambda r: _robots_status(status))
    with pytest.raises(RobotsUnavailableError):
        load_robots(fetcher, "https://x.co.il", UA)


def test_robots_network_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(RobotsUnavailableError, match="ConnectError"):
        load_robots(make_fetcher(fail), "https://x.co.il", UA)


# -- sitemaps ---------------------------------------------------------------------

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://x.co.il/a/</loc><lastmod>2026-09-01T10:00:00+03:00</lastmod></url>
  <url><loc> https://x.co.il/b/ </loc><lastmod>2026-09-02</lastmod></url>
  <url><loc>https://x.co.il/c/</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.co.il/s1.xml.gz</loc><lastmod>2026-09-01Z</lastmod></sitemap>
</sitemapindex>"""


def test_urlset_and_lastmod_utc() -> None:
    sitemap = parse_sitemap(URLSET)
    assert not sitemap.is_index
    assert [e.loc for e in sitemap.entries] == [
        "https://x.co.il/a/", "https://x.co.il/b/", "https://x.co.il/c/"
    ]
    assert sitemap.entries[0].lastmod == datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    assert sitemap.entries[1].lastmod == datetime(2026, 9, 2, tzinfo=UTC)
    assert sitemap.entries[2].lastmod is None


def test_sitemapindex_and_gzip() -> None:
    index = parse_sitemap(INDEX)
    assert index.is_index and index.entries[0].loc == "https://x.co.il/s1.xml.gz"
    assert parse_sitemap(gzip.compress(URLSET)).entries[0].loc == "https://x.co.il/a/"


def test_invalid_sitemap() -> None:
    with pytest.raises(SitemapError):
        parse_sitemap(b"<html><body>oops</body></html>")
    with pytest.raises(SitemapError):
        parse_sitemap(b"not xml at all")
    assert parse_lastmod("garbage") is None


# -- links ------------------------------------------------------------------------

RULES = UrlNormalizationRules(trailing_slash=TrailingSlash.KEEP)
PAGE = b"""<html><body>
<a href="/a">A</a> <a href="b/#part">B</a> <a href="#top">top</a>
<a href="mailto:x@x.co.il">m</a> <a href="tel:123">t</a> <a href="javascript:void(0)">j</a>
<a href="https://other.co.il/x">off</a> <a href="https://www.x.co.il/files/Guide.PDF">pdf</a>
<a href="/a/">dup</a> <a>no href</a>
<map><area href="/area/"></map>
<a href="/%D7%A8%D7%9B%D7%91/">heb</a>
</body></html>"""


def test_extract_links() -> None:
    links = extract_links(PAGE, "https://www.x.co.il/dir/page/", RULES, "www.x.co.il")
    urls = [link.url.url_display for link in links]
    assert urls == [
        "https://www.x.co.il/a/",
        "https://www.x.co.il/dir/page/b/",
        "https://www.x.co.il/files/Guide.PDF",
        "https://www.x.co.il/area/",
        "https://www.x.co.il/רכב/",
    ]
    assert [link.is_pdf for link in links] == [False, False, True, False, False]


def test_extract_links_base_href_and_empty() -> None:
    page = b'<html><head><base href="/base/"></head><body><a href="x">x</a></body></html>'
    links = extract_links(page, "https://x.co.il/p/", RULES, "x.co.il")
    assert [link.url.url for link in links] == ["https://x.co.il/base/x/"]
    assert extract_links(b"", "https://x.co.il/", RULES, "x.co.il") == []


# -- raw store --------------------------------------------------------------------


def test_raw_store(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    assert not store.exists("u1", ContentType.HTML)
    store.save("u1", ContentType.HTML, b"<html>\xd7\x90</html>")
    assert store.read("u1", ContentType.HTML) == b"<html>\xd7\x90</html>"
    store.save("u1", ContentType.PDF, b"%PDF-1.4")
    assert store.exists("u1", ContentType.PDF) and not store.exists("u1", ContentType.HTML)
    assert not store.exists("u1", None)


# -- rate limiter -----------------------------------------------------------------


def _limiter(time: FakeTime, rand: float = 0.0, **kw: float) -> RateLimiter:
    return RateLimiter(1.5, 0.5, 2.0, clock=time.clock, sleep=time.sleep, rand=lambda: rand, **kw)


@pytest.mark.parametrize("rand", [0.0, 0.5, 0.999])
def test_delay_within_base_and_jitter(rand: float) -> None:
    time = FakeTime()
    limiter = _limiter(time, rand)
    limiter.wait()
    assert time.sleeps == []  # first request: no wait
    limiter.record(0.1)
    limiter.wait()
    assert 1.5 <= time.sleeps[0] <= 2.0
    assert time.sleeps[0] == pytest.approx(1.5 + 0.5 * rand)


def test_delay_counts_from_end_of_previous_request() -> None:
    time = FakeTime()
    limiter = _limiter(time)
    limiter.record(0.1)
    time.now += 1.0  # e.g. processing the page took 1s
    limiter.wait()
    assert time.sleeps == [pytest.approx(0.5)]


def test_crawl_delay_respected() -> None:
    time = FakeTime()
    limiter = _limiter(time)
    limiter.set_crawl_delay(5)
    assert limiter.base_delay == 5
    limiter.set_crawl_delay(1)  # smaller than the profile delay: profile wins
    assert limiter.base_delay == 1.5


def test_adaptive_doubling_with_hysteresis() -> None:
    time = FakeTime()
    limiter = _limiter(time, baseline_size=4, window_size=4)
    for _ in range(4):
        limiter.record(0.2)  # baseline 0.2s
    assert not limiter.slowed
    for _ in range(4):
        limiter.record(0.4)  # ratio 2.0
    assert limiter.slowed
    assert limiter.next_delay() == pytest.approx(3.0)
    for _ in range(4):
        limiter.record(0.32)  # ratio 1.6: still slow (hysteresis)
    assert limiter.slowed
    for _ in range(4):
        limiter.record(0.28)  # ratio 1.4: recovered
    assert not limiter.slowed
    assert limiter.next_delay() == pytest.approx(1.5)
