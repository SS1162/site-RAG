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
