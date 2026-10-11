"""Composition root: reads configuration and builds the concrete objects (manual DI)."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from siterag.adapters.sqlalchemy.engine import create_session_factory, create_sqlite_engine
from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.config.loader import file_hash, load_settings, load_site_profile, site_profile_path
from siterag.config.models import Settings, SiteProfile
from siterag.domain.urls import UrlNormalizationRules
from siterag.ports.unit_of_work import UnitOfWork

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_SITES_DIR = Path("sites")


class Container:
    """Holds the configuration of one site and builds its dependencies on demand."""

    def __init__(
        self,
        settings: Settings,
        site: SiteProfile,
        config_hash: str,
        site_profile_hash: str,
    ) -> None:
        self.settings = settings
        self.site = site
        self.config_hash = config_hash
        self.site_profile_hash = site_profile_hash

    @property
    def site_data_dir(self) -> Path:
        """Isolated data directory of the site: ``<data_root>/<site_id>``."""
        return self.settings.data_root / self.site.site_id

    @property
    def db_path(self) -> Path:
        """Path of the site's ``state.db``."""
        return self.site_data_dir / "state.db"

    @property
    def url_rules(self) -> UrlNormalizationRules:
        """URL normalization rules of the site."""
        return self.site.url_normalization.to_rules()

    @cached_property
    def engine(self) -> Engine:
        """SQLite engine for ``state.db`` (created on first use)."""
        self.site_data_dir.mkdir(parents=True, exist_ok=True)
        return create_sqlite_engine(self.db_path)

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        """Session factory bound to the engine."""
        return create_session_factory(self.engine)

    def uow(self) -> UnitOfWork:
        """Return a new Unit of Work."""
        return SqlAlchemyUnitOfWork(self.session_factory, self.settings.pipeline.stage_inputs)

    def close(self) -> None:
        """Release database connections."""
        if "engine" in self.__dict__:
            self.engine.dispose()


def build_container(
    site_id: str,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    sites_dir: Path = DEFAULT_SITES_DIR,
) -> Container:
    """Load and validate configuration for ``site_id`` and return its container."""
    profile_path = site_profile_path(sites_dir, site_id)
    return Container(
        settings=load_settings(settings_path),
        site=load_site_profile(profile_path),
        config_hash=file_hash(settings_path),
        site_profile_hash=file_hash(profile_path),
    )
