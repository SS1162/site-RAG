"""URL normalization. All site-specific behaviour comes from ``UrlNormalizationRules``."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

from siterag.domain.hashing import make_url_id

DEFAULT_TRACKING_PARAMS: tuple[str, ...] = ("utm_*", "fbclid", "gclid", "mc_cid", "mc_eid", "_ga")
_DEFAULT_PORTS = {80, 443}
# Characters allowed unencoded inside a path segment (RFC 3986 pchar minus "/").
_PATH_SAFE = "!$&'()*+,;=:@-._~"
# Characters allowed unencoded inside a query key or value.
_QUERY_SAFE = "!$'()*,;:@/?-._~"


@dataclass(frozen=True)
class UrlNormalizationRules:
    """Site-specific URL normalization settings (from the site profile)."""

    strip_www: bool = False
    tracking_params: tuple[str, ...] = DEFAULT_TRACKING_PARAMS
    keep_params: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NormalizedUrl:
    """Result of normalization: encoded URL, human-readable form and stable id."""

    url: str
    url_display: str
    url_id: str


def _normalize_host(hostname: str, port: int | None, rules: UrlNormalizationRules) -> str:
    host = hostname.lower()
    if rules.strip_www and host.startswith("www."):
        host = host[4:]
    if port is not None and port not in _DEFAULT_PORTS:
        host = f"{host}:{port}"
    return host


def _normalize_path(path: str) -> tuple[str, str]:
    """Return (encoded, display) path with duplicate slashes and trailing slash removed."""
    segments = [unquote(s) for s in path.split("/") if s]
    if not segments:
        return "/", "/"
    encoded = "/" + "/".join(quote(s, safe=_PATH_SAFE) for s in segments)
    display = "/" + "/".join(segments)
    return encoded, display


def _keep_param(name: str, rules: UrlNormalizationRules) -> bool:
    if rules.keep_params:
        return name in rules.keep_params
    return not any(fnmatchcase(name, pattern) for pattern in rules.tracking_params)


def _normalize_query(query: str, rules: UrlNormalizationRules) -> tuple[str, str]:
    """Return (encoded, display) query: filtered and sorted by parameter name."""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if _keep_param(k, rules)]
    pairs.sort(key=lambda kv: kv[0])  # stable: values of a repeated key keep their order
    encoded = "&".join(
        f"{quote(k, safe=_QUERY_SAFE)}={quote(v, safe=_QUERY_SAFE)}" for k, v in pairs
    )
    display = "&".join(f"{k}={v}" for k, v in pairs)
    return encoded, display


def normalize_url(raw: str, rules: UrlNormalizationRules) -> NormalizedUrl:
    """Normalize a URL so that equivalent spellings map to the same ``url_id``.

    Forces https, lowercases the host, drops default ports, the fragment, duplicate and
    trailing slashes, and tracking parameters (or keeps only ``keep_params`` when set).
    Percent-encoding is decoded and re-applied consistently, so already encoded and raw
    non-ASCII URLs produce the same result without double encoding.
    """
    parts = urlsplit(raw.strip())
    if not parts.hostname:
        raise ValueError(f"URL has no host: {raw!r}")
    host = _normalize_host(parts.hostname, parts.port, rules)
    path, path_display = _normalize_path(parts.path)
    query, query_display = _normalize_query(parts.query, rules)

    url = urlunsplit(("https", host, path, query, ""))
    url_display = urlunsplit(("https", host, path_display, query_display, ""))
    return NormalizedUrl(url=url, url_display=url_display, url_id=make_url_id(url))
