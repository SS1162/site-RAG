"""Sitemap parsing: ``<urlset>``, ``<sitemapindex>`` and gzipped files."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime

from lxml import etree

from siterag.ports.fetcher import Sitemap, SitemapEntry

_GZIP_MAGIC = b"\x1f\x8b"
# No network access, no entity expansion: sitemap content is untrusted.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


class SitemapError(ValueError):
    """The file is not a valid sitemap."""


def parse_lastmod(value: str | None) -> datetime | None:
    """Parse a W3C datetime (``2026-01-31`` or full ISO-8601) into UTC; None if invalid.

    A value without a timezone is taken as UTC.
    """
    if not value or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _local(tag: object) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def parse_sitemap(content: bytes) -> Sitemap:
    """Parse sitemap bytes (gzip is detected by its magic number)."""
    if content[:2] == _GZIP_MAGIC:
        try:
            content = gzip.decompress(content)
        except (OSError, EOFError) as exc:
            raise SitemapError(f"invalid gzip data: {exc}") from exc
    try:
        root = etree.fromstring(content, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise SitemapError(f"invalid XML: {exc}") from exc

    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"unexpected root element <{kind}>")
    child_tag = "sitemap" if kind == "sitemapindex" else "url"
    entries: list[SitemapEntry] = []
    for child in root:
        if _local(child.tag) != child_tag:
            continue
        fields = {_local(el.tag): (el.text or "").strip() for el in child}
        if fields.get("loc"):
            entries.append(SitemapEntry(fields["loc"], parse_lastmod(fields.get("lastmod"))))
    return Sitemap(is_index=kind == "sitemapindex", entries=entries)
