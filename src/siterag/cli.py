"""Command-line interface."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from siterag.adapters.sqlalchemy import migrate
from siterag.container import DEFAULT_SETTINGS_PATH, DEFAULT_SITES_DIR, Container, build_container
from siterag.domain.enums import RunStatus
from siterag.domain.errors import ConfigError, RunLockedError
from siterag.pipeline.context import RunContext, RunFlags
from siterag.pipeline.report import latest_report
from siterag.pipeline.runner import select_stages

app = typer.Typer(help="site-rag: RAG over the content of a public website.", no_args_is_help=True)
db_app = typer.Typer(help="Manage a site's state.db.", no_args_is_help=True)
app.add_typer(db_app, name="db")

SiteOption = Annotated[str, typer.Option("--site", help="Site id (sites/<site_id>.yaml).")]
SettingsOption = Annotated[
    Path, typer.Option("--settings", help="Global settings file.")
]
SitesDirOption = Annotated[Path, typer.Option("--sites-dir", help="Directory of site profiles.")]


EXIT_CODES = {RunStatus.COMPLETED: 0, RunStatus.FAILED: 1, RunStatus.ABORTED: 3}


def _container(site: str, settings: Path, sites_dir: Path) -> Container:
    try:
        return build_container(site, settings, sites_dir)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _setup_logging(container: Container) -> None:
    logging.basicConfig(
        level=container.settings.logging.level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _print_scope_summary(ctx: RunContext) -> None:
    stats = ctx.frontier.stats
    typer.echo("Dry run: discovery only, nothing was stored.")
    typer.echo(f"Discovered (in scope): {stats.discovered}")
    typer.echo(f"Blocked by robots.txt: {stats.robots_blocked}")
    typer.echo(f"Other host: {stats.offsite}   invalid: {stats.invalid}   "
               f"over max_pages: {stats.over_cap}")
    typer.echo(f"Excluded by rule: {sum(stats.excluded_by_rule.values())}")
    for rule, count in stats.excluded_by_rule.most_common():
        typer.echo(f"  {count:>6}  {rule}")
    typer.echo("By page_type:")
    for name, count in stats.page_types.most_common():
        typer.echo(f"  {count:>6}  {name}")
    typer.echo("By domain:")
    for name, count in stats.domains.most_common():
        typer.echo(f"  {count:>6}  {name}")
    for warning in ctx.warnings:
        typer.secho(f"Warning: {warning}", fg=typer.colors.YELLOW)


@db_app.command("upgrade")
def db_upgrade(
    site: SiteOption,
    settings: SettingsOption = DEFAULT_SETTINGS_PATH,
    sites_dir: SitesDirOption = DEFAULT_SITES_DIR,
) -> None:
    """Apply database migrations to data/<site_id>/state.db."""
    container = _container(site, settings, sites_dir)
    migrate.upgrade(container.db_path)
    typer.echo(f"Database is up to date: {container.db_path}")


@db_app.command("status")
def db_status(
    site: SiteOption,
    settings: SettingsOption = DEFAULT_SETTINGS_PATH,
    sites_dir: SitesDirOption = DEFAULT_SITES_DIR,
) -> None:
    """Print page counts by status, chunk count and the last run summary."""
    container = _container(site, settings, sites_dir)
    if not container.db_path.is_file():
        typer.secho(
            f"No database at {container.db_path}; run 'db upgrade --site {site}' first.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        with container.uow() as uow:
            by_status = uow.pages.count_by_status()
            active = uow.pages.count_active()
            chunk_count = uow.chunks.count()
            last_run = uow.runs.get_last()
            events = uow.events.summary(last_run.run_id) if last_run else {}
    finally:
        container.close()

    typer.echo(f"Site: {site}  ({container.db_path})")
    typer.echo(f"Pages: {sum(by_status.values())} total, {active} active")
    for status, count in sorted(by_status.items()):
        typer.echo(f"  {status.value:<18}{count}")
    typer.echo(f"Chunks: {chunk_count}")
    if last_run is None:
        typer.echo("Last run: none")
        return
    finished = last_run.finished_at.isoformat() if last_run.finished_at else "-"
    typer.echo(
        f"Last run: {last_run.run_id}  {last_run.command}  {last_run.status.value}\n"
        f"  started {last_run.started_at.isoformat()}  finished {finished}"
    )
    if last_run.abort_reason:
        typer.echo(f"  abort reason: {last_run.abort_reason}")
    typer.echo(
        f"  new={last_run.pages_new} changed={last_run.pages_changed} "
        f"unchanged={last_run.pages_unchanged} removed={last_run.pages_removed} "
        f"failed={last_run.pages_failed} redirected={last_run.pages_redirected}"
    )
    for event, count in sorted(events.items()):
        typer.echo(f"  event {event.value:<16}{count}")


@app.command("run")
def run(
    site: SiteOption,
    only: Annotated[str | None, typer.Option("--only", help="Run only this stage.")] = None,
    from_: Annotated[
        str | None, typer.Option("--from", help="Run this stage and the following ones.")
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Fetch at most N pages (disables removals)."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Discovery only, rolled back; print scope summary.")
    ] = False,
    allow_mass_removal: Annotated[
        bool, typer.Option("--allow-mass-removal", help="Bypass the removal threshold.")
    ] = False,
    settings: SettingsOption = DEFAULT_SETTINGS_PATH,
    sites_dir: SitesDirOption = DEFAULT_SITES_DIR,
) -> None:
    """Run the configured pipeline stages for a site."""
    container = _container(site, settings, sites_dir)
    _setup_logging(container)
    try:
        stage_names = select_stages(container.settings.pipeline.stages, only, from_)
        migrate.upgrade(container.db_path)
        flags = RunFlags(
            limit=limit,
            allow_mass_removal=allow_mass_removal,
            discovery_ran="discover" in stage_names,
        )
        runner = container.runner()
        if dry_run:
            _print_scope_summary(runner.dry_run(flags))
            return
        args = {"only": only, "from": from_, "limit": limit,
                "allow_mass_removal": allow_mass_removal}
        outcome = runner.run(stage_names, flags, args)
    except (ConfigError, RunLockedError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    finally:
        container.close()

    final = outcome.run
    typer.echo(
        f"Run {final.run_id}: {final.status.value}"
        + (f" ({final.abort_reason})" if final.abort_reason else "")
    )
    typer.echo(
        f"  new={final.pages_new} changed={final.pages_changed} "
        f"unchanged={final.pages_unchanged} removed={final.pages_removed} "
        f"redirected={final.pages_redirected} skipped={final.pages_skipped} "
        f"orphaned={final.pages_orphaned} failed={final.pages_failed}"
    )
    typer.echo(f"Report: {outcome.report}")
    raise typer.Exit(code=EXIT_CODES[final.status])


@app.command("report")
def report(
    site: SiteOption,
    settings: SettingsOption = DEFAULT_SETTINGS_PATH,
    sites_dir: SitesDirOption = DEFAULT_SITES_DIR,
) -> None:
    """Print the latest run report of a site."""
    container = _container(site, settings, sites_dir)
    path = latest_report(container.reports_dir)
    if path is None:
        typer.secho(f"No run report in {container.reports_dir}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
    typer.echo(path.read_text(encoding="utf-8"))
