"""Pydantic models for global settings and site profiles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from siterag.domain.classify import ClassificationRules
from siterag.domain.scope import ScopeRules
from siterag.domain.urls import DEFAULT_TRACKING_PARAMS, TrailingSlash, UrlNormalizationRules


class _Strict(BaseModel):
    """Base model: unknown keys are errors (catches typos in YAML)."""

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class LoggingSettings(_Strict):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class PipelineSettings(_Strict):
    """Stage wiring: which page column feeds each stage, and each stage's logic version."""

    stages: list[str] = Field(default_factory=list)
    """Stages run by ``siterag run``, in order."""
    stage_inputs: dict[str, str] = Field(min_length=1)
    versions: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def _every_stage_has_version(self) -> PipelineSettings:
        missing = sorted(set(self.stage_inputs) - set(self.versions))
        if missing:
            raise ValueError(f"pipeline.versions is missing stages: {', '.join(missing)}")
        return self


class Settings(_Strict):
    """Global settings (config/settings.yaml)."""

    data_root: Path = Path("data")
    logging: LoggingSettings = LoggingSettings()
    pipeline: PipelineSettings


class UrlNormalizationConfig(_Strict):
    """URL normalization section of a site profile."""

    strip_www: bool = False
    trailing_slash: TrailingSlash = TrailingSlash.STRIP
    tracking_params: list[str] = Field(default_factory=lambda: list(DEFAULT_TRACKING_PARAMS))
    keep_params: list[str] = Field(default_factory=list)

    def to_rules(self) -> UrlNormalizationRules:
        """Convert to the domain rules object."""
        return UrlNormalizationRules(
            strip_www=self.strip_www,
            trailing_slash=self.trailing_slash,
            tracking_params=tuple(self.tracking_params),
            keep_params=tuple(self.keep_params),
        )


class SafetyConfig(_Strict):
    """Removal safety guard thresholds."""

    removal_max_ratio: float = Field(default=0.15, gt=0, le=1)
    discovery_min_pages: int = Field(ge=0)


class FailureConfig(_Strict):
    """Across-run failure policy."""

    max_consecutive_failed_runs: int = Field(default=3, ge=1)


class RunLockConfig(_Strict):
    """Run lock settings."""

    stale_after_hours: float = Field(default=12, gt=0)


PLACEHOLDER = "<FILL_ME>"


def _check_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
    return pattern


class HttpConfig(_Strict):
    """HTTP client and politeness settings."""

    user_agent_name: str = "site-rag"
    user_agent_version: str = "0.1"
    contact_url: str = PLACEHOLDER
    contact_email: str = PLACEHOLDER
    base_delay_s: float = Field(default=1.5, ge=0)
    jitter_s: float = Field(default=0.5, ge=0)
    adaptive_factor: float = Field(default=2.0, ge=1)
    connect_timeout_s: float = Field(default=10, gt=0)
    read_timeout_s: float = Field(default=30, gt=0)
    max_redirects: int = Field(default=5, ge=0)
    accept_language: str = "he"
    max_retries: int = Field(default=3, ge=0)
    circuit_breaker_threshold: int = Field(default=10, ge=1)

    @property
    def user_agent(self) -> str:
        """Honest, identifiable User-Agent: ``name/version (+url; email)``."""
        return (
            f"{self.user_agent_name}/{self.user_agent_version} "
            f"(+{self.contact_url}; {self.contact_email})"
        )

    def placeholder_fields(self) -> list[str]:
        """Names of contact fields still set to the placeholder."""
        return [
            name
            for name in ("contact_url", "contact_email")
            if PLACEHOLDER in getattr(self, name)
        ]


class ScopeConfig(_Strict):
    """Which URLs are crawled and which documents are downloaded."""

    max_link_depth: int = Field(default=2, ge=0)
    max_pages: int = Field(default=5000, ge=1)
    max_file_mb: float = Field(default=20, gt=0)
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["text/html", "application/pdf"]
    )
    skip_extensions: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    """Regular expressions searched in the decoded URL path."""

    @field_validator("exclude")
    @classmethod
    def _regexes(cls, value: list[str]) -> list[str]:
        return [_check_regex(p) for p in value]

    def to_rules(self) -> ScopeRules:
        """Compile the exclude rules."""
        return ScopeRules.compile(self.exclude)


class PageTypeRule(_Strict):
    """``page_type`` given to paths matching ``pattern``."""

    pattern: str
    type: str

    @field_validator("pattern")
    @classmethod
    def _regex(cls, value: str) -> str:
        return _check_regex(value)


class DomainRule(_Strict):
    """``domain`` given to paths matching ``pattern``."""

    pattern: str
    domain: str

    @field_validator("pattern")
    @classmethod
    def _regex(cls, value: str) -> str:
        return _check_regex(value)


class ClassificationConfig(_Strict):
    """Ordered classification rules; the first match wins, the fallback is ``general``."""

    page_types: list[PageTypeRule] = Field(default_factory=list)
    domains: list[DomainRule] = Field(default_factory=list)

    def to_rules(self) -> ClassificationRules:
        """Compile the rules."""
        return ClassificationRules.compile(
            [(r.pattern, r.type) for r in self.page_types],
            [(r.pattern, r.domain) for r in self.domains],
        )


class SiteProfile(_Strict):
    """Everything site-specific (sites/<site_id>.yaml)."""

    site_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    base_url: str
    url_normalization: UrlNormalizationConfig = UrlNormalizationConfig()
    safety: SafetyConfig
    failure: FailureConfig = FailureConfig()
    run_lock: RunLockConfig = RunLockConfig()
    sitemaps: list[str] = Field(default_factory=list)
    """Fallback sitemap URLs, used when robots.txt declares none."""
    seeds: list[str] = Field(default_factory=list)
    """Extra start URLs; the site root is always a seed."""
    http: HttpConfig = HttpConfig()
    scope: ScopeConfig = ScopeConfig()
    classification: ClassificationConfig = ClassificationConfig()

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value
