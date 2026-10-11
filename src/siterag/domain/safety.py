"""Guards that stop a run before it wrongly removes large parts of the site."""

from __future__ import annotations

from siterag.domain.errors import DiscoveryTooSmallError, RemovalThresholdExceededError


def check_discovery(found_count: int, min_pages: int) -> None:
    """Raise ``DiscoveryTooSmallError`` if discovery found fewer than ``min_pages`` URLs."""
    if found_count < min_pages:
        raise DiscoveryTooSmallError(
            f"Discovery found {found_count} URLs, fewer than the minimum of {min_pages}"
        )


def check_removals(candidates_count: int, active_count: int, max_ratio: float) -> None:
    """Raise ``RemovalThresholdExceededError`` if candidates / active exceeds ``max_ratio``."""
    if active_count <= 0 or candidates_count <= 0:
        return
    ratio = candidates_count / active_count
    if ratio > max_ratio:
        raise RemovalThresholdExceededError(
            f"{candidates_count} of {active_count} active pages ({ratio:.1%}) are removal "
            f"candidates, above the limit of {max_ratio:.1%}"
        )
