"""Page classification by ordered regex rules on the decoded path. Pure functions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

FALLBACK = "general"
"""Value used when no rule matches."""

Rule = tuple[re.Pattern[str], str]


@dataclass(frozen=True)
class ClassificationRules:
    """Ordered ``(pattern, value)`` rules for ``page_type`` and ``domain``; first match wins."""

    page_types: tuple[Rule, ...] = ()
    domains: tuple[Rule, ...] = ()

    @classmethod
    def compile(
        cls, page_types: Iterable[tuple[str, str]], domains: Iterable[tuple[str, str]]
    ) -> ClassificationRules:
        """Build rules from ``(regex, value)`` pairs."""
        return cls(
            tuple((re.compile(p), v) for p, v in page_types),
            tuple((re.compile(p), v) for p, v in domains),
        )


def _first_match(path: str, rules: tuple[Rule, ...]) -> str:
    for pattern, value in rules:
        if pattern.search(path):
            return value
    return FALLBACK


def classify(path: str, rules: ClassificationRules) -> tuple[str, str]:
    """Return ``(page_type, domain)`` of a decoded path."""
    return _first_match(path, rules.page_types), _first_match(path, rules.domains)
