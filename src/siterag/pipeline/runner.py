"""Runs the configured stages in order: run lock, finish status and run report."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siterag.config.models import Settings, SiteProfile
from siterag.domain.entities import Run
from siterag.domain.enums import RunStatus
from siterag.domain.errors import ConfigError, DiscoveryTooSmallError, RunAbortedError
from siterag.pipeline.context import RunContext, RunFlags
from siterag.pipeline.report import LISTED_EVENTS, render_report, report_path
from siterag.pipeline.stages.discover import DiscoverStage
from siterag.ports.stage import Stage
from siterag.ports.unit_of_work import UnitOfWork

ContextFactory = Callable[[str, RunFlags], RunContext]


def select_stages(
    configured: Sequence[str], only: str | None = None, from_: str | None = None
) -> list[str]:
    """Stages to run: all configured ones, ``--only <stage>`` or ``--from <stage>``."""
    if only and from_:
        raise ConfigError("--only and --from cannot be used together")
    for name in (only, from_):
        if name is not None and name not in configured:
            raise ConfigError(
                f"Unknown stage {name!r}; configured stages: {', '.join(configured)}"
            )
    if only:
        return [only]
    if from_:
        return list(configured[configured.index(from_):])
    return list(configured)


@dataclass(frozen=True)
class RunOutcome:
    """Final run row and where its report was written."""

    run: Run
    report: Path
    context: RunContext


class PipelineRunner:
    """Executes stages by name. Adding a stage needs no change here (see container.py)."""

    def __init__(
        self,
        stages: Mapping[str, Stage],
        settings: Settings,
        site: SiteProfile,
        config_hash: str,
        site_profile_hash: str,
        uow_factory: Callable[[], UnitOfWork],
        make_context: ContextFactory,
        reports_dir: Path,
    ) -> None:
        self._stages = dict(stages)
        self._settings = settings
        self._site = site
        self._config_hash = config_hash
        self._profile_hash = site_profile_hash
        self._uow = uow_factory
        self._make_context = make_context
        self._reports_dir = reports_dir

    def run(self, stage_names: Sequence[str], flags: RunFlags, args: dict[str, Any]) -> RunOutcome:
        """Run ``stage_names`` in order. Re-raises unexpected errors after writing the report."""
        unknown = [n for n in stage_names if n not in self._stages]
        if unknown:
            raise ConfigError(f"Stages not registered: {', '.join(unknown)}")

        with self._uow() as uow:
            run = uow.runs.start(self._site.site_id, "run", args, self._config_hash,
                                 self._profile_hash, self._site.run_lock.stale_after_hours)
            for stage, version in self._settings.pipeline.versions.items():
                uow.pages.reset_failures_for_version_change(stage, version, run.run_id)
            uow.commit()
        ctx = self._make_context(run.run_id, flags)

        status, reason, error = RunStatus.COMPLETED, None, None
        try:
            for name in stage_names:
                ctx.logger.info("Stage %s: start", name)
                result = self._stages[name].run(ctx)
                ctx.results.append(result)
                ctx.logger.info("Stage %s: done %s", name, result.summary)
        except DiscoveryTooSmallError as exc:
            status, reason = RunStatus.ABORTED, "discovery_too_small"
            ctx.warn(str(exc))
        except RunAbortedError as exc:
            status, reason = RunStatus.ABORTED, exc.reason
            ctx.warn(f"Run aborted ({exc.reason}): {exc}")
        except Exception as exc:  # noqa: BLE001 - recorded, reported, then re-raised
            status, reason, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}", exc
        finally:
            outcome = self._finish(ctx, status, reason)
        if error is not None:
            raise error
        return outcome

    def _finish(self, ctx: RunContext, status: RunStatus, reason: str | None) -> RunOutcome:
        with self._uow() as uow:
            run = uow.runs.finish(ctx.run_id, status, reason)
            events = uow.events.list(ctx.run_id)
            urls: dict[str, str] = {}
            for event in events:
                if event.event in LISTED_EVENTS and event.url_id not in urls:
                    page = uow.pages.get(event.url_id)
                    urls[event.url_id] = page.url_display if page else event.url_id
            uow.commit()
        text = render_report(run, events, urls, ctx.fetch_stats, ctx.frontier.stats,
                             ctx.warnings)
        path = report_path(self._reports_dir, run)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        ctx.logger.info("Run %s %s; report: %s", run.run_id, run.status.value, path)
        return RunOutcome(run, path, ctx)

    def dry_run(self, flags: RunFlags) -> RunContext:
        """Run discovery in a transaction that is rolled back; nothing is stored."""
        stage = self._stages.get("discover")
        if not isinstance(stage, DiscoverStage):
            raise ConfigError("--dry-run needs the 'discover' stage")
        with self._uow() as uow:
            run = uow.runs.start(self._site.site_id, "dry-run", {}, self._config_hash,
                                 self._profile_hash, self._site.run_lock.stale_after_hours)
            ctx = self._make_context(run.run_id, flags)
            try:
                stage.discover(ctx, uow)
            except DiscoveryTooSmallError as exc:
                ctx.warn(str(exc))
            uow.rollback()
        return ctx

