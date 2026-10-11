"""Command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from siterag.adapters.sqlalchemy import migrate
from siterag.container import DEFAULT_SETTINGS_PATH, DEFAULT_SITES_DIR, Container, build_container
from siterag.domain.errors import ConfigError

app = typer.Typer(help="site-rag: RAG over the content of a public website.", no_args_is_help=True)
db_app = typer.Typer(help="Manage a site's state.db.", no_args_is_help=True)
app.add_typer(db_app, name="db")

SiteOption = Annotated[str, typer.Option("--site", help="Site id (sites/<site_id>.yaml).")]
SettingsOption = Annotated[
    Path, typer.Option("--settings", help="Global settings file.")
]
SitesDirOption = Annotated[Path, typer.Option("--sites-dir", help="Directory of site profiles.")]


def _container(site: str, settings: Path, sites_dir: Path) -> Container:
    try:
        return build_container(site, settings, sites_dir)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


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
