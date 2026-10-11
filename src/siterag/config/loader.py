"""Load and validate YAML configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from siterag.config.models import Settings, SiteProfile
from siterag.domain.errors import ConfigError
from siterag.domain.hashing import sha256_hex

_M = TypeVar("_M", bound=BaseModel)


def file_hash(path: Path) -> str:
    """Return the SHA-256 of a file's raw bytes (recorded on each run)."""
    return sha256_hex(path.read_bytes())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def _validate(model: type[_M], data: dict[str, Any], path: Path) -> _M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"  - {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ConfigError(f"Invalid configuration in {path}:\n" + "\n".join(lines)) from exc


def load_settings(path: Path) -> Settings:
    """Load the global settings file."""
    return _validate(Settings, _read_yaml(path), path)


def site_profile_path(sites_dir: Path, site_id: str) -> Path:
    """Return the path of a site's profile file."""
    return sites_dir / f"{site_id}.yaml"


def load_site_profile(path: Path) -> SiteProfile:
    """Load a site profile; its ``site_id`` must match the file name."""
    profile = _validate(SiteProfile, _read_yaml(path), path)
    if profile.site_id != path.stem:
        raise ConfigError(
            f"site_id {profile.site_id!r} in {path} does not match the file name {path.stem!r}"
        )
    return profile
