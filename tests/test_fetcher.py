"""HttpxFetcher over httpx.MockTransport: no real network, no real waiting."""

import httpx
import pytest

from siterag.domain.enums import ContentType
from siterag.domain.errors import CircuitOpenError
from siterag.ports.fetcher import SkipReason
from tests.fakes import FakeTime, make_fetcher

HTML = b"<html><body>\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d</body></html>"


def html(status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, content=HTML, headers={"Content-Type": "text/html", **headers})


def test_200_html_and_request_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return html(ETag='"v1"')

    result = make_fetcher(handler).fetch("https://x.co.il/a/", etag='"v0"',
                                         last_modified="Mon, 01 Sep 2026 00:00:00 GMT")
    assert result.status == 200 and result.content == HTML
    assert result.content_type == ContentType.HTML and result.headers["etag"] == '"v1"'
    assert seen[0].headers["If-None-Match"] == '"v0"'
    assert seen[0].headers["If-Modified-Since"] == "Mon, 01 Sep 2026 00:00:00 GMT"
    assert seen[0].headers["User-Agent"] == "site-rag-test/0.1"


def test_304_counted_in_stats() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(304))
    result = fetcher.fetch("https://x.co.il/a/", etag='"v1"')
    assert result.status == 304 and result.content is None
    assert fetcher.stats.requests == 1 and fetcher.stats.not_modified == 1


def test_same_host_redirect_drops_validators() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/old/":
            return httpx.Response(301, headers={"Location": "/new/"})
        return html()

    result = make_fetcher(handler).fetch("https://x.co.il/old/", etag='"v0"')
    assert result.status == 200 and result.final_url == "https://x.co.il/new/"
    assert result.redirect_status == 301
    assert "If-None-Match" not in seen[1].headers


def test_offsite_redirect_skipped() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(302, headers={"Location": "https://evil.com/"}))
    result = fetcher.fetch("https://x.co.il/a/")
    assert result.skip_reason == SkipReason.OFFSITE_REDIRECT
    assert result.final_url == "https://evil.com/"


def test_too_many_redirects() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(302, headers={"Location": "/loop/"}),
                           max_redirects=2)
    assert "more than 2 redirects" in (fetcher.fetch("https://x.co.il/a/").error or "")


def test_image_content_type_skipped() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(200, content=b"\x89PNG",
                                                    headers={"Content-Type": "image/png"}))
    assert fetcher.fetch("https://x.co.il/a/").skip_reason == SkipReason.CONTENT_TYPE


def test_non_documents_allowed_when_not_documents_only() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(200, content=b"<urlset/>",
                                                    headers={"Content-Type": "application/xml"}))
    result = fetcher.fetch("https://x.co.il/sitemap.xml", documents_only=False)
    assert result.status == 200 and result.content == b"<urlset/>"


@pytest.mark.parametrize(
    ("body", "expected"),
    [(b"%PDF-1.7 ...", ContentType.PDF), (b"\n<!DOCTYPE html><html>", ContentType.HTML)],
)
def test_missing_content_type_is_sniffed(body: bytes, expected: ContentType) -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(200, content=body))
    assert fetcher.fetch("https://x.co.il/f").content_type == expected


def test_unrecognised_body_without_content_type_skipped() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(200, content=b"GIF89a"))
    assert fetcher.fetch("https://x.co.il/f").skip_reason == SkipReason.CONTENT_TYPE


def test_too_large_by_header_and_by_stream() -> None:
    big = b"%PDF-" + b"x" * 200

    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"Content-Type": "application/pdf"})

    def streamed(request: httpx.Request) -> httpx.Response:
        def chunks():  # type: ignore[no-untyped-def]
            yield big[:100]
            yield big[100:]
        return httpx.Response(200, content=chunks(), headers={"Content-Type": "application/pdf"})

    for handler in (declared, streamed):
        result = make_fetcher(handler, max_bytes=150).fetch("https://x.co.il/f.pdf")
        assert result.skip_reason == SkipReason.TOO_LARGE and result.content is None


def test_503_then_200_after_backoff() -> None:
    time = FakeTime()
    responses = iter([httpx.Response(503), httpx.Response(503), html()])
    fetcher = make_fetcher(lambda r: next(responses), time)
    result = fetcher.fetch("https://x.co.il/a/")
    assert result.status == 200
    # backoff sleeps 2s and 4s (rate-limiter sleeps are 1.5s each)
    assert [s for s in time.sleeps if s != 1.5] == [2.0, 4.0]


def test_retries_exhausted_returns_last_status() -> None:
    time = FakeTime()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(502)

    result = make_fetcher(handler, time).fetch("https://x.co.il/a/")
    assert result.status == 502 and len(calls) == 4  # 1 attempt + 3 retries
    assert [s for s in time.sleeps if s != 1.5] == [2.0, 4.0, 8.0]


def test_no_retry_on_other_4xx() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400)

    assert make_fetcher(handler).fetch("https://x.co.il/a/").status == 400
    assert len(calls) == 1


def test_network_error_retried_then_reported() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    result = make_fetcher(handler).fetch("https://x.co.il/a/")
    assert result.error is not None and "ReadTimeout" in result.error
    assert len(calls) == 4


def test_retry_after_honoured_and_capped() -> None:
    time = FakeTime()
    responses = iter([httpx.Response(429, headers={"Retry-After": "10"}), html()])
    assert make_fetcher(lambda r: next(responses), time).fetch("https://x.co.il/a/").status == 200
    assert 10.0 in time.sleeps

    calls: list[int] = []

    def too_long(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, headers={"Retry-After": "3600"})

    assert make_fetcher(too_long).fetch("https://x.co.il/a/").status == 503
    assert len(calls) == 1  # gave up instead of waiting an hour


def test_circuit_breaker_counts_every_refusal_and_resets_on_success() -> None:
    status = {"code": 429}
    fetcher = make_fetcher(lambda r: httpx.Response(status["code"]), circuit_breaker_threshold=10)
    fetcher.fetch("https://x.co.il/a/")  # 4 attempts -> 4 refusals
    status["code"] = 200
    fetcher.fetch("https://x.co.il/b/")  # success resets the counter
    status["code"] = 403
    for _ in range(9):
        fetcher.fetch("https://x.co.il/c/")  # 403 is not retried: 1 refusal each
    with pytest.raises(CircuitOpenError):
        fetcher.fetch("https://x.co.il/d/")
