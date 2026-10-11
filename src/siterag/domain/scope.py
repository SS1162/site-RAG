"""Scope rules: which URLs of the site take part in the pipeline. Pure functions.

Everything on the site is in scope unless its decoded path matches an exclude rule
(deny-list). Host and robots.txt checks are done by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class ScopeRules:
    """Compiled exclude rules (regular expressions searched in the decoded path)."""

    exclude: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def compile(cls, patterns: Iterable[str]) -> ScopeRules:
        """Build rules from regex strings."""
        return cls(tuple(re.compile(p) for p in patterns))


def decoded_path(url: str) -> str:
    """Return the percent-decoded path of a URL (``/`` when empty)."""
    return unquote(urlsplit(url).path) or "/"


def in_scope(path: str, rules: ScopeRules) -> tuple[bool, str | None]:
    """Return ``(True, None)``, or ``(False, pattern)`` for the first exclude rule matching."""
    for pattern in rules.exclude:
        if pattern.search(path):
            return False, pattern.pattern
    return True, None


def has_extension(path: str, extensions: Iterable[str]) -> bool:
    """True when the last path segment ends with one of ``extensions`` (case-insensitive)."""
    last = path.rstrip("/").rsplit("/", 1)[-1].lower()
    return any(last.endswith(ext.lower()) for ext in extensions)
