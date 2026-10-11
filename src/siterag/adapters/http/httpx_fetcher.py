"""``Fetcher`` implementation with httpx: streaming, conditional requests, redirects, size cap.

Redirects are followed by hand, one hop at a time, so that each hop's host is checked and
the requested page's validators are not sent to a different URL.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
)

from siterag.adapters.http.rate_limiter import RateLimiter
from siterag.domain.enums import ContentType
from siterag.domain.errors import CircuitOpenError
from siterag.ports.fetcher import FetchResult, FetchStats, SkipReason

log = logging.getLogger(__name__)

REFUSAL_STATUSES = frozenset({403, 429})
"""Responses counted by the circuit breaker."""
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
"""Content-Types that say nothing; the body is sniffed instead."""


@dataclass
class _Response:
    """One HTTP response (one hop, one attempt)."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    content_type: ContentType | None = None
    skip_reason: SkipReason | None = None
    location: str | None = None


def _retryable(response: _Response) -> bool:
    return response.status == 429 or response.status >= 500


def _mime(headers: httpx.Headers) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _type_of_mime(mime: str) -> ContentType:
    if mime == "application/pdf":
        return ContentType.PDF
    return ContentType.HTML if "html" in mime else ContentType.OTHER


def sniff(content: bytes) -> ContentType | None:
    """Guess HTML or PDF from the first bytes; None when neither."""
    head = content[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"%pdf-"):
        return ContentType.PDF
    if head.startswith((b"<!doctype html", b"<html")):
        return ContentType.HTML
    return None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class HttpxFetcher:
    """Polite, single-connection fetcher. See ``ports.fetcher.Fetcher``."""

    def __init__(
        self,
        client: httpx.Client,
        rate_limiter: RateLimiter,
        *,
        allowed_content_types: Collection[str],
        max_bytes: int,
        max_redirects: int = 5,
        max_retries: int = 3,
        circuit_breaker_threshold: int = 10,
        backoff_base_s: float = 2.0,
        retry_after_cap_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._rate = rate_limiter
        self._allowed = {t.lower() for t in allowed_content_types}
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._max_retries = max_retries
        self._breaker_threshold = circuit_breaker_threshold
        self._backoff_base = backoff_base_s
        self._retry_after_cap = retry_after_cap_s
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._refusals = 0
        self.stats = FetchStats()

    # -- public API ----------------------------------------------------------------

    def set_crawl_delay(self, seconds: float | None) -> None:
        """Apply robots.txt ``Crawl-delay``."""
        self._rate.set_crawl_delay(seconds)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        documents_only: bool = True,
    ) -> FetchResult:
        """Fetch ``url`` following at most ``max_redirects`` same-host redirects."""
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        start = self._clock()
        current = url
        redirect_status: int | None = None

        def result(**kwargs: object) -> FetchResult:
            return FetchResult(
                requested_url=url,
                final_url=current,
                redirect_status=redirect_status,
                elapsed_s=self._clock() - start,
                **kwargs,  # type: ignore[arg-type]
            )

        for _ in range(self._max_redirects + 1):
            try:
                response = self._send_with_retries(current, headers, documents_only)
            except httpx.HTTPError as exc:
                return result(error=f"{type(exc).__name__}: {exc}".rstrip(": "))
            if response.location is None:
                return result(
                    status=response.status,
                    headers=response.headers,
                    content=response.content,
                    content_type=response.content_type,
                    skip_reason=response.skip_reason,
                )
            if redirect_status is None:
                redirect_status = response.status
            if _host(response.location) != _host(url):
                current = response.location
                return result(skip_reason=SkipReason.OFFSITE_REDIRECT)
            current = response.location
            headers = {}  # validators belong to the requested URL, not to the target
        return result(error=f"more than {self._max_redirects} redirects")

    # -- retries -------------------------------------------------------------------

    def _retry_after(self, state: RetryCallState) -> float | None:
        if state.outcome is None or state.outcome.failed:
            return None
        value = state.outcome.result().headers.get("retry-after")
        if not value:
            return None
        if value.strip().isdigit():
            return float(value.strip())
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - self._now()).total_seconds())

    def _wait(self, state: RetryCallState) -> float:
        backoff = self._backoff_base * 2 ** (state.attempt_number - 1)  # 2, 4, 8 s
        return max(backoff, self._retry_after(state) or 0.0)

    def _retry_after_too_long(self, state: RetryCallState) -> bool:
        delay = self._retry_after(state)
        if delay is not None and delay > self._retry_after_cap:
            log.warning("Retry-After %.0fs is above the %.0fs cap: giving up", delay,
                        self._retry_after_cap)
            return True
        return False

    def _send_with_retries(self, url: str, headers: dict[str, str], documents_only: bool
                           ) -> _Response:
        retrying = Retrying(
            stop=stop_after_attempt(self._max_retries + 1) | self._retry_after_too_long,
            wait=self._wait,
            retry=retry_if_exception_type(httpx.TransportError) | retry_if_result(_retryable),
            sleep=self._sleep,
            # When retries are exhausted: return the last response or raise the last error.
            retry_error_callback=lambda state: state.outcome.result(),  # type: ignore[union-attr]
        )
        return retrying(self._send, url, headers, documents_only)

    # -- one request ---------------------------------------------------------------

    def _send(self, url: str, headers: dict[str, str], documents_only: bool) -> _Response:
        self._rate.wait()
        start = self._clock()
        try:
            with self._client.stream("GET", url, headers=headers) as resp:
                response = self._read(resp, url, documents_only)
        finally:
            elapsed = self._clock() - start
            self._rate.record(elapsed)
        self.stats.requests += 1
        self.stats.total_elapsed_s += elapsed
        if response.status == 304:
            self.stats.not_modified += 1
        self._update_breaker(response.status, url)
        return response

    def _update_breaker(self, status: int, url: str) -> None:
        if status in REFUSAL_STATUSES:
            self._refusals += 1
            if self._refusals >= self._breaker_threshold:
                raise CircuitOpenError(
                    f"{self._refusals} consecutive 429/403 responses (last: {url})"
                )
        elif status < 400:
            self._refusals = 0

    def _read(self, resp: httpx.Response, url: str, documents_only: bool) -> _Response:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        status = resp.status_code
        if resp.is_redirect and "location" in headers:
            return _Response(status, headers, location=urljoin(url, headers["location"]))
        if status != 200:
            return _Response(status, headers)

        mime = _mime(resp.headers)
        content_type: ContentType | None = None
        if documents_only:
            if mime in self._allowed:
                content_type = _type_of_mime(mime)
            elif mime not in _GENERIC_TYPES:
                return _Response(status, headers, skip_reason=SkipReason.CONTENT_TYPE)

        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > self._max_bytes:
            return _Response(status, headers, skip_reason=SkipReason.TOO_LARGE)
        body = bytearray()
        for chunk in resp.iter_bytes():
            body.extend(chunk)
            if len(body) > self._max_bytes:
                return _Response(status, headers, skip_reason=SkipReason.TOO_LARGE)
        content = bytes(body)

        if documents_only and content_type is None:
            content_type = sniff(content)
            if content_type is None:
                return _Response(status, headers, skip_reason=SkipReason.CONTENT_TYPE)
        return _Response(status, headers, content=content, content_type=content_type)
