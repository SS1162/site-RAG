from pathlib import Path

import pytest

from siterag.config.loader import load_settings, load_site_profile
from siterag.domain.errors import ConfigError
from siterag.domain.urls import TrailingSlash

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_files_are_valid() -> None:
    settings = load_settings(ROOT / "config" / "settings.yaml")
    assert settings.pipeline.stage_inputs["extract"] == "raw_hash"
    assert settings.pipeline.versions["fetch"] == "1"
    profile = load_site_profile(ROOT / "sites" / "fnx.yaml")
    assert profile.site_id == "fnx"
    rules = profile.url_normalization.to_rules()
    assert rules.strip_www is False
    assert rules.trailing_slash == TrailingSlash.KEEP


def test_unknown_key_fails_with_clear_message(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "pipeline: {stage_inputs: {a: raw_hash}, versions: {a: 1}}\ntypo_key: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="typo_key"):
        load_settings(path)


def test_stage_without_version_rejected(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "pipeline: {stage_inputs: {a: raw_hash}, versions: {b: '1'}}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="missing stages: a"):
        load_settings(path)


def test_site_id_must_match_file_name(tmp_path: Path) -> None:
    path = tmp_path / "other.yaml"
    path.write_text(
        "site_id: fnx\nbase_url: https://x.co.il\nsafety: {discovery_min_pages: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="does not match"):
        load_site_profile(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")


def write_profile(tmp_path: Path, url_normalization: str) -> Path:
    path = tmp_path / "demo.yaml"
    path.write_text(
        "site_id: demo\nbase_url: https://x.co.il\nsafety: {discovery_min_pages: 1}\n"
        + url_normalization,
        encoding="utf-8",
    )
    return path


def test_trailing_slash_defaults_to_strip(tmp_path: Path) -> None:
    profile = load_site_profile(write_profile(tmp_path, ""))
    assert profile.url_normalization.to_rules().trailing_slash == TrailingSlash.STRIP


def test_invalid_trailing_slash_rejected(tmp_path: Path) -> None:
    path = write_profile(tmp_path, "url_normalization: {trailing_slash: sometimes}\n")
    with pytest.raises(ConfigError, match="trailing_slash"):
        load_site_profile(path)


def test_invalid_scope_regex_rejected(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(
        "site_id: demo\nbase_url: https://x.co.il\nsafety: {discovery_min_pages: 1}\n"
        "scope: {exclude: ['(unclosed']}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="scope.exclude"):
        load_site_profile(path)


def test_shipped_pipeline_stages() -> None:
    settings = load_settings(ROOT / "config" / "settings.yaml")
    assert settings.pipeline.stages == ["discover", "fetch"]
