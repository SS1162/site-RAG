"""Domain exceptions."""

from __future__ import annotations


class SiteRagError(Exception):
    """Base class for all site-rag errors."""


class ConfigError(SiteRagError):
    """Configuration file is missing or invalid."""


class RunLockedError(SiteRagError):
    """Another run is in progress for this site."""


class NotFoundError(SiteRagError):
    """A requested entity does not exist."""


class DiscoveryTooSmallError(SiteRagError):
    """Discovery found fewer URLs than the configured minimum."""


class RemovalThresholdExceededError(SiteRagError):
    """Too large a share of active pages would be removed in one run."""


class RunAbortedError(SiteRagError):
    """The run must stop; ``reason`` is recorded as the run's ``abort_reason``."""

    reason = "aborted"


class RobotsUnavailableError(RunAbortedError):
    """robots.txt could not be read (5xx or network error), so nothing may be crawled."""

    reason = "robots_unavailable"


class CircuitOpenError(RunAbortedError):
    """The server refused too many consecutive requests (429/403)."""

    reason = "circuit_open"
