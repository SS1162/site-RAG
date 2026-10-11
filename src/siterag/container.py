"""Composition root: reads configuration and builds the concrete objects (manual DI)."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable
from functools import cached_property
from pathlib import Path

import httpx
import truststore
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from siterag.adapters.http.httpx_fetcher import HttpxFetcher
from siterag.adapters.http.rate_limiter import RateLimiter
from siterag.adapters.http.robots import ProtegoRobots, load_robots
from siterag.adapters.http.sitemap import parse_sitemap
from siterag.adapters.sqlalchemy.engine import create_session_factory, create_sqlite_engine
from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.config.loader import file_hash, load_settings, load_site_profile, site_profile_path
from siterag.config.models import Settings, SiteProfile
from siterag.domain.entities import utc_now
from siterag.domain.urls import UrlNormalizationRules
from siterag.pipeline.context import RunContext, RunFlags
from siterag.pipeline.frontier import Frontier, site_host
from siterag.pipeline.runner import PipelineRunner
from siterag.pipeline.stages.discover import DiscoverStage
from siterag.pipeline.stages.fetch import FetchStage
from siterag.ports.fetcher import Fetcher
from siterag.ports.stage import Stage
from siterag.ports.unit_of_work import UnitOfWork
from siterag.storage.raw_store import RawStore

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_SITES_DIR = Path("sites")
MB = 1024 * 1024

STAGE_REGISTRY: dict[str, Callable[[Container], Stage]] = {
    "discover": lambda c: DiscoverStage(c.fetcher, c.load_robots, parse_sitemap),
    "fetch": lambda c: FetchStage(c.fetcher, c.raw_store, c.load_robots),
}
"""Stage name -> factory. The run order comes from ``pipeline.stages`` in settings.yaml."""


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

    @property
    def reports_dir(self) -> Path:
        """Run reports: ``<site_data_dir>/reports``."""
        return self.site_data_dir / "reports"

    @cached_property
    def raw_store(self) -> RawStore:
        """Raw downloaded files: ``<site_data_dir>/raw``."""
        return RawStore(self.site_data_dir / "raw")

    @cached_property
    def fetcher(self) -> Fetcher:
        """HTTP fetcher. TLS uses the operating system's trust store (truststore)."""
        http = self.site.http
        client = httpx.Client(
            headers={"User-Agent": http.user_agent, "Accept-Language": http.accept_language},
            timeout=httpx.Timeout(http.read_timeout_s, connect=http.connect_timeout_s),
            follow_redirects=False,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        rate = RateLimiter(http.base_delay_s, http.jitter_s, http.adaptive_factor)
        return HttpxFetcher(
            client,
            rate,
            allowed_content_types=self.site.scope.allowed_content_types,
            max_bytes=int(self.site.scope.max_file_mb * MB),
            max_redirects=http.max_redirects,
            max_retries=http.max_retries,
            circuit_breaker_threshold=http.circuit_breaker_threshold,
        )

    def load_robots(self) -> ProtegoRobots:
        """Fetch and parse the site's robots.txt."""
        return load_robots(self.fetcher, self.site.base_url, self.site.http.user_agent_name)

    @cached_property
    def stages(self) -> dict[str, Stage]:
        """Every registered stage, built once."""
        return {name: factory(self) for name, factory in STAGE_REGISTRY.items()}

    def startup_warnings(self) -> list[str]:
        """Configuration problems that do not prevent a run."""
        placeholders = self.site.http.placeholder_fields()
        if not placeholders:
            return []
        return [
            f"Site profile http.{', http.'.join(placeholders)} still contain <FILL_ME>: "
            "the User-Agent does not identify a contact"
        ]

    def make_context(self, run_id: str, flags: RunFlags) -> RunContext:
        """Fresh per-run state for the stages."""
        logger = logging.getLogger(f"siterag.run.{self.site.site_id}")
        warnings: list[str] = []

        def warn(message: str) -> None:
            logger.warning(message)
            warnings.append(message)

        for message in self.startup_warnings():
            warn(message)
        self.fetcher.stats.reset()
        frontier = Frontier(
            run_id=run_id,
            site_host=site_host(self.site.base_url, self.url_rules),
            url_rules=self.url_rules,
            scope_rules=self.site.scope.to_rules(),
            classification=self.site.classification.to_rules(),
            max_link_depth=self.site.scope.max_link_depth,
            max_pages=self.site.scope.max_pages,
            warn=warn,
        )
        return RunContext(
            run_id=run_id,
            settings=self.settings,
            site=self.site,
            uow_factory=self.uow,
            flags=flags,
            frontier=frontier,
            fetch_stats=self.fetcher.stats,
            clock=utc_now,
            logger=logger,
            warnings=warnings,
        )

    def runner(self) -> PipelineRunner:
        """Pipeline runner over the registered stages."""
        return PipelineRunner(
            stages=self.stages,
            settings=self.settings,
            site=self.site,
            config_hash=self.config_hash,
            site_profile_hash=self.site_profile_hash,
            uow_factory=self.uow,
            make_context=self.make_context,
            reports_dir=self.reports_dir,
        )

    def close(self) -> None:
        """Release database and network connections."""
        if "fetcher" in self.__dict__:
            self.fetcher.close()
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
