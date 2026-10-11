"""Build a real Container over a temporary data dir, with the fetcher pointed at a FakeSite."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from siterag.adapters.sqlalchemy import migrate
from siterag.container import Container, build_container
from siterag.domain.entities import Page
from siterag.domain.enums import EventType
from siterag.pipeline.context import RunFlags
from siterag.pipeline.runner import RunOutcome, select_stages
from tests.fakes import BASE, FakeSite, FakeTime, make_fetcher

ROOT = Path(__file__).resolve().parents[1]

PROFILE = """\
site_id: demo
base_url: {base}
url_normalization: {{trailing_slash: keep}}
safety: {{removal_max_ratio: {ratio}, discovery_min_pages: {min_pages}}}
failure: {{max_consecutive_failed_runs: 3}}
http:
  user_agent_name: site-rag-test
  contact_url: https://example.org/repo
  contact_email: test@example.org
scope:
  max_link_depth: 2
  max_pages: {max_pages}
  max_file_mb: 1
  skip_extensions: [.jpg]
  exclude: {exclude}
classification:
  page_types:
    - {{pattern: '\\.pdf$', type: document}}
    - {{pattern: '^/a/', type: product}}
  domains:
    - {{pattern: '^/a/', domain: car}}
"""


def make_container(
    tmp_path: Path,
    site: FakeSite,
    *,
    exclude: Sequence[str] = (),
    ratio: float = 0.5,
    min_pages: int = 3,
    max_pages: int = 100,
) -> Container:
    """Container for site ``demo`` whose fetcher talks to ``site`` (no network, no waiting)."""
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir(exist_ok=True)
    exclude_yaml = "[" + ", ".join(f"'{e}'" for e in exclude) + "]"
    (sites_dir / "demo.yaml").write_text(
        PROFILE.format(base=BASE, ratio=ratio, min_pages=min_pages, max_pages=max_pages,
                       exclude=exclude_yaml),
        encoding="utf-8",
    )
    settings = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        settings.replace("data_root: data", f"data_root: {(tmp_path / 'data').as_posix()}"),
        encoding="utf-8",
    )
    container = build_container("demo", settings_path, sites_dir)
    migrate.upgrade(container.db_path)
    container.__dict__["fetcher"] = make_fetcher(site.handler, FakeTime(), max_bytes=1024 * 1024)
    return container


def run(
    container: Container,
    *,
    only: str | None = None,
    from_: str | None = None,
    limit: int | None = None,
    allow_mass_removal: bool = False,
) -> RunOutcome:
    """Run the pipeline like the CLI does."""
    names = select_stages(container.settings.pipeline.stages, only, from_)
    flags = RunFlags(limit=limit, allow_mass_removal=allow_mass_removal,
                     discovery_ran="discover" in names)
    return container.runner().run(names, flags, {})


def page(container: Container, path: str) -> Page:
    """The stored page for ``BASE + path`` (fails if missing)."""
    from siterag.domain.urls import normalize_url

    url = normalize_url(BASE + path, container.url_rules)
    with container.uow() as uow:
        found = uow.pages.get(url.url_id)
    assert found is not None, f"page {path} not stored"
    return found


def events(container: Container, run_id: str) -> dict[str, list[EventType]]:
    """Events of a run per display path."""
    result: dict[str, list[EventType]] = {}
    with container.uow() as uow:
        for event in uow.events.list(run_id):
            stored = uow.pages.get(event.url_id)
            assert stored is not None
            path = stored.url_display.removeprefix(BASE)
            result.setdefault(path, []).append(event.event)
    return result
