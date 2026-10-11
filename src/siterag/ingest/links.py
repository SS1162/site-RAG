"""Extract same-site links from HTML bytes."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import etree, html

from siterag.domain.scope import decoded_path
from siterag.domain.urls import NormalizedUrl, UrlNormalizationRules, normalize_url

LINK_TAGS = ("a", "area")


@dataclass(frozen=True)
class Link:
    """A normalized same-host link found on a page."""

    url: NormalizedUrl
    is_pdf: bool


def _hrefs(content: bytes) -> tuple[str | None, list[str]]:
    """Return the ``<base href>`` and the ``href`` of every link element, in order."""
    if not content.strip():
        return None, []
    try:
        doc = html.document_fromstring(content)
    except (etree.ParserError, ValueError):
        return None, []
    base = next((el.get("href") for el in doc.iter("base") if el.get("href")), None)
    hrefs = [el.get("href", "") for el in doc.iter(*LINK_TAGS)]
    return base, hrefs


def extract_links(
    content: bytes, page_url: str, rules: UrlNormalizationRules, host: str
) -> list[Link]:
    """Return the distinct links of an HTML page that stay on ``host``.

    Relative links are resolved against ``<base href>`` (if any) or ``page_url``.
    Fragments are dropped; ``mailto:``, ``tel:``, ``javascript:`` and other non-HTTP
    schemes, fragment-only links and links to other hosts are ignored.
    ``host`` is compared with the normalized host of each link.
    """
    base_href, hrefs = _hrefs(content)
    base = urljoin(page_url, base_href.strip()) if base_href else page_url
    links: dict[str, Link] = {}
    for href in hrefs:
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        absolute, _ = urldefrag(urljoin(base, href))
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        try:
            normalized = normalize_url(absolute, rules)
        except ValueError:
            continue
        if urlsplit(normalized.url).hostname != host or normalized.url_id in links:
            continue
        is_pdf = decoded_path(normalized.url).lower().endswith(".pdf")
        links[normalized.url_id] = Link(normalized, is_pdf)
    return list(links.values())
