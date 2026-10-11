"""Pydantic models for global settings and site profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from siterag.domain.urls import DEFAULT_TRACKING_PARAMS, TrailingSlash, UrlNormalizationRules


class _Strict(BaseModel):
    """Base model: unknown keys are errors (catches typos in YAML)."""

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class LoggingSettings(_Strict):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class PipelineSettings(_Strict):
    """Stage wiring: which page column feeds each stage, and each stage's logic version."""

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


class SiteProfile(_Strict):
    """Everything site-specific (sites/<site_id>.yaml)."""

    site_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    base_url: str
    url_normalization: UrlNormalizationConfig = UrlNormalizationConfig()
    safety: SafetyConfig
    failure: FailureConfig = FailureConfig()
    run_lock: RunLockConfig = RunLockConfig()

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value
