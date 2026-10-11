"""State shared by the stages of one run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from siterag.config.models import Settings, SiteProfile
from siterag.pipeline.frontier import Frontier
from siterag.ports.fetcher import FetchStats
from siterag.ports.stage import StageResult
from siterag.ports.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class RunFlags:
    """Command-line options that change how stages behave."""

    limit: int | None = None
    """Fetch at most this many pages; disables the removal phase."""
    allow_mass_removal: bool = False
    discovery_ran: bool = True
    """False with ``--only fetch`` / ``--from fetch``: nothing was "seen", no removals."""


@dataclass
class RunContext:
    """Everything a stage needs to know about the current run."""

    run_id: str
    settings: Settings
    site: SiteProfile
    uow_factory: Callable[[], UnitOfWork]
    flags: RunFlags
    frontier: Frontier
    fetch_stats: FetchStats
    clock: Callable[[], datetime]
    logger: logging.Logger
    warnings: list[str] = field(default_factory=list)
    results: list[StageResult] = field(default_factory=list)

    def warn(self, message: str) -> None:
        """Log a warning and keep it for the run report."""
        self.logger.warning(message)
        self.warnings.append(message)

    def stage_version(self, stage: str) -> str:
        """Configured logic version of a stage (``"1"`` when not configured)."""
        return self.settings.pipeline.versions.get(stage, "1")
