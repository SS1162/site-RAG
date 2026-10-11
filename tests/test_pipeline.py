"""Discover + fetch stages, removal phase, dry run and runner, on the FakeSite."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from siterag.container import Container
from siterag.domain.enums import EventType, PageStatus, RunStatus
from siterag.pipeline.context import RunFlags
from siterag.pipeline.report import latest_report
from tests.fakes import FakeSite, Route, html_page
from tests.pipeline_helpers import events, make_container, page, run

E = EventType


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def container(tmp_path: Path, site: FakeSite) -> Iterator[Container]:
    container = make_container(tmp_path, site)
    yield container
    container.close()


# -- first run (end to end) -------------------------------------------------------


def test_first_run_end_to_end(container: Container, site: FakeSite) -> None:
    outcome = run(container)
    final = outcome.run
    assert final.status == RunStatus.COMPLETED

    # statuses
    for path in ("/", "/a/", "/a/info/", "/b/", "/hidden/", "/a/deep1/", "/new/",
                 "/files/doc.pdf", "/files/deep.pdf"):
        assert page(container, path).status == PageStatus.FETCHED, path
    assert page(container, "/old/").status == PageStatus.REDIRECTED
    assert page(container, "/old/").redirected_to == page(container, "/new/").url_id
    assert page(container, "/gone/").status == PageStatus.REMOVED
    assert page(container, "/photo.jpg").status == PageStatus.SKIPPED

    # depth rules: HTML at depth 3 not admitted; PDF linked from depth 2 admitted
    assert "/a/deep1/deep2/" not in site.paths_requested()
    assert page(container, "/a/deep1/").discovery_depth == 2
    assert page(container, "/files/deep.pdf").discovery_depth == 3
    assert page(container, "/new/").discovery_depth == 0  # same depth as /old/
    assert page(container, "/new/").found_on == page(container, "/old/").url_id
    # robots-blocked, mailto and other-host links never requested
    assert not any("page=" in p for p in site.paths_requested())

    # classification
    pdf = page(container, "/files/doc.pdf")
    assert (pdf.page_type, pdf.domain) == ("document", "general")
    assert (page(container, "/a/").page_type, page(container, "/a/").domain) == ("product", "car")

    # raw files stored exactly as received
    raw = container.raw_store
    assert raw.read(pdf.url_id, pdf.content_type) == b"%PDF-1.4 doc"  # type: ignore[arg-type]

    # events and counters
    ev = events(container, final.run_id)
    assert ev["/"] == [E.NEW]
    assert ev["/old/"] == [E.REDIRECTED]
    assert ev["/new/"] == [E.NEW]
    assert ev["/gone/"] == [E.REMOVED]
    assert ev["/photo.jpg"] == [E.SKIPPED]
    assert (final.pages_new, final.pages_removed, final.pages_redirected,
            final.pages_skipped, final.pages_failed) == (9, 1, 1, 1, 0)

    # report
    report = outcome.report.read_text(encoding="utf-8")
    assert outcome.report == latest_report(container.reports_dir)
    assert "Status: **completed**" in report
    assert "| 9 | 0 | 0 | 1 | 1 | 1 | 0 | 0 |" in report
    assert "photo.jpg | extension" in report


def test_second_run_uses_304_and_keeps_link_only_children_seen(
    container: Container, site: FakeSite
) -> None:
    run(container)
    site.requests.clear()
    second = run(container).run
    ev = events(container, second.run_id)
    assert ev["/"] == [E.UNCHANGED]
    # /hidden/ is only linked from / (a 304): links were re-read from the raw file
    assert ev["/hidden/"] == [E.UNCHANGED]
    assert page(container, "/hidden/").last_seen_run_id == second.run_id
    # /gone/ is still in the sitemap: restored on discovery, removed again on its 404
    assert ev["/gone/"] == [E.RESTORED, E.REMOVED]
    assert second.pages_removed == 1 and second.pages_orphaned == 0
    root = next(r for r in site.requests if r.url.path == "/")
    assert "If-None-Match" in root.headers
    assert container.fetcher.stats.not_modified > 0
    # stats are per run: robots + sitemap + every page once (no 200s left to count twice)
    assert container.fetcher.stats.requests == len(site.requests)


def test_changed_page(container: Container, site: FakeSite) -> None:
    run(container)
    site.routes["/b/"] = Route(body=html_page(title="b v2"))
    second = run(container).run
    assert events(container, second.run_id)["/b/"] == [E.CHANGED]
    assert second.pages_changed == 1


def test_304_with_missing_raw_file_refetches(container: Container, site: FakeSite) -> None:
    run(container)
    root = page(container, "/")
    container.raw_store.path(root.url_id, root.content_type).unlink()  # type: ignore[arg-type]
    site.requests.clear()
    run(container)
    request = next(r for r in site.requests if r.url.path == "/")
    assert "If-None-Match" not in request.headers  # unconditional
    assert container.raw_store.exists(root.url_id, root.content_type)


def test_too_large_and_content_type_skips(container: Container, site: FakeSite) -> None:
    site.routes["/b/"] = Route(body=b"%PDF-" + b"x" * (1024 * 1024 + 10),
                               content_type="application/pdf")
    site.routes["/hidden/"] = Route(body=b"GIF89a", content_type="image/gif")
    final = run(container).run
    with container.uow() as uow:
        skipped = {e.details["reason"] for e in uow.events.list(final.run_id, E.SKIPPED)}
    assert skipped == {"too_large", "content_type", "extension"}
    assert page(container, "/b/").status == PageStatus.SKIPPED


def test_offsite_and_out_of_scope_redirects(container: Container, site: FakeSite) -> None:
    site.routes["/b/"] = Route(status=302, headers={"Location": "https://evil.co.il/"},
                               content_type=None)
    site.routes["/old/"] = Route(status=301, headers={"Location": "/x/?page=3"},
                                 content_type=None)
    final = run(container).run
    with container.uow() as uow:
        reasons = {e.details["reason"] for e in uow.events.list(final.run_id, E.SKIPPED)}
    assert {"offsite_redirect", "out_of_scope_redirect"} <= reasons
    assert page(container, "/b/").status == PageStatus.SKIPPED


def test_retry_then_success_and_failure_policy(container: Container, site: FakeSite) -> None:
    calls = {"n": 0}
    original = site.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/b/":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503)
        if request.url.path == "/hidden/":
            return httpx.Response(500)
        return original(request)

    site.handler = flaky  # type: ignore[method-assign]
    container.__dict__.pop("fetcher")
    from tests.fakes import FakeTime, make_fetcher

    container.__dict__["fetcher"] = make_fetcher(flaky, FakeTime())
    container.__dict__.pop("stages", None)
    final = run(container).run
    assert page(container, "/b/").status == PageStatus.FETCHED
    hidden = page(container, "/hidden/")
    assert hidden.status == PageStatus.FAILED and hidden.fail_count == 1
    assert hidden.last_error == "HTTP 500" and final.pages_failed == 1


def test_circuit_breaker_aborts_without_failing_pages(
    container: Container, site: FakeSite
) -> None:
    original = site.handler

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/robots.txt", "/sitemap.xml"):
            return original(request)
        return httpx.Response(429)

    from tests.fakes import FakeTime, make_fetcher

    container.__dict__["fetcher"] = make_fetcher(refuse, FakeTime())
    container.__dict__.pop("stages", None)
    outcome = run(container)
    assert outcome.run.status == RunStatus.ABORTED
    assert outcome.run.abort_reason == "circuit_open"
    assert outcome.run.pages_failed == 0
    with container.uow() as uow:
        assert PageStatus.FAILED not in uow.pages.count_by_status()


def test_robots_500_aborts_run(container: Container, site: FakeSite) -> None:
    site.routes["/robots.txt"] = Route(status=500, content_type=None)
    outcome = run(container)
    assert outcome.run.status == RunStatus.ABORTED
    assert outcome.run.abort_reason == "robots_unavailable"


def test_robots_404_allows_all(container: Container, site: FakeSite) -> None:
    del site.routes["/robots.txt"]
    container.site.sitemaps.append("https://www.x.co.il/sitemap.xml")  # profile fallback
    outcome = run(container)
    assert outcome.run.status == RunStatus.COMPLETED
    assert "/x/?page=2" in site.paths_requested()


def test_discovery_too_small_aborts(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site, min_pages=50)
    try:
        outcome = run(container)
    finally:
        container.close()
    assert outcome.run.abort_reason == "discovery_too_small"
    assert "/a/" not in site.paths_requested()


def test_max_pages_cap(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site, max_pages=6)
    try:
        outcome = run(container)
        with container.uow() as uow:
            total = sum(uow.pages.count_by_status().values())
    finally:
        container.close()
    assert outcome.run.status == RunStatus.COMPLETED
    assert total <= 7  # 6 admitted + the redirect target (bypasses the cap)
    assert any("max_pages (6) reached" in w for w in outcome.context.warnings)


# -- removal phase ----------------------------------------------------------------


def _seed_orphan(container: Container, site: FakeSite) -> None:
    """First run in which /orphan/ is linked; then the link disappears."""
    site.routes["/b/"] = Route(body=html_page("/orphan/"))
    run(container)
    site.routes["/b/"] = Route(body=html_page(title="b"))


def test_orphan_stays_and_is_reported(container: Container, site: FakeSite) -> None:
    _seed_orphan(container, site)
    before = page(container, "/orphan/")
    outcome = run(container)
    after = page(container, "/orphan/")
    assert events(container, outcome.run.run_id)["/orphan/"] == [E.ORPHAN]
    assert after.status == PageStatus.FETCHED
    assert after.last_seen_run_id == before.last_seen_run_id  # not "seen"
    assert after.last_fetch_ok_run_id == outcome.run.run_id
    assert outcome.run.pages_orphaned == 1
    assert "## Orphans (1)" in outcome.report.read_text(encoding="utf-8")


def test_candidate_404_removed(container: Container, site: FakeSite) -> None:
    _seed_orphan(container, site)
    del site.routes["/orphan/"]
    outcome = run(container)
    assert page(container, "/orphan/").status == PageStatus.REMOVED
    assert events(container, outcome.run.run_id)["/orphan/"] == [E.REMOVED]


def test_newly_excluded_page_removed_without_request(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site)
    run(container)
    container.close()
    container = make_container(tmp_path, site, exclude=["^/b/"])
    try:
        site.requests.clear()
        outcome = run(container)
        with container.uow() as uow:
            removed = uow.events.list(outcome.run.run_id, E.REMOVED)
    finally:
        container.close()
    assert "/b/" not in site.paths_requested()
    assert any(e.details == {"reason": "out_of_scope", "rule": "^/b/"} for e in removed)


def test_threshold_exceeded_skips_removals(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site, ratio=0.01)
    try:
        _seed_orphan(container, site)
        del site.routes["/orphan/"]
        outcome = run(container)
    finally:
        container.close()
    assert outcome.run.status == RunStatus.COMPLETED
    assert outcome.run.pages_removed == 1  # only /gone/, found during fetch (404)
    assert any("Removal phase skipped" in w for w in outcome.context.warnings)
    assert "Removal phase skipped" in outcome.report.read_text(encoding="utf-8")


def test_allow_mass_removal_bypasses_threshold(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site, ratio=0.01)
    try:
        _seed_orphan(container, site)
        del site.routes["/orphan/"]
        outcome = run(container, allow_mass_removal=True)
        assert page(container, "/orphan/").status == PageStatus.REMOVED
    finally:
        container.close()
    assert outcome.run.pages_removed == 2  # /orphan/ + /gone/ (still in the sitemap)


def test_limit_disables_removal_phase(container: Container, site: FakeSite) -> None:
    _seed_orphan(container, site)
    del site.routes["/orphan/"]
    site.requests.clear()
    outcome = run(container, limit=2)
    page_requests = [p for p in site.paths_requested()
                     if p not in ("/robots.txt", "/sitemap.xml")]
    assert len(page_requests) == 2
    assert page(container, "/orphan/").status == PageStatus.FETCHED
    assert any("Removal phase disabled" in w for w in outcome.context.warnings)


def test_only_fetch_queues_active_pages_without_removals(
    container: Container, site: FakeSite
) -> None:
    _seed_orphan(container, site)
    del site.routes["/orphan/"]
    site.requests.clear()
    outcome = run(container, only="fetch")
    assert "/sitemap.xml" not in site.paths_requested()
    assert "/b/" in site.paths_requested()
    # /orphan/ is active, so it was fetched as part of the queue: 404 → removed by fetch
    assert page(container, "/orphan/").status == PageStatus.REMOVED
    assert any("discovery did not run" in w for w in outcome.context.warnings)


def test_redirected_page_restored_when_it_returns_200(
    container: Container, site: FakeSite
) -> None:
    run(container)
    site.routes["/old/"] = Route(body=html_page(title="old is back"))
    second = run(container).run
    old = page(container, "/old/")
    assert old.status == PageStatus.FETCHED and old.redirected_to is None
    assert E.RESTORED in events(container, second.run_id)["/old/"]


# -- dry run ----------------------------------------------------------------------


def test_dry_run_leaves_database_unchanged(tmp_path: Path, site: FakeSite) -> None:
    container = make_container(tmp_path, site, exclude=["^/b/"])
    try:
        ctx = container.runner().dry_run(RunFlags())
        with container.uow() as uow:
            assert uow.pages.count_by_status() == {}
            assert uow.runs.get_last() is None
    finally:
        container.close()
    stats = ctx.frontier.stats
    assert stats.discovered == 4  # /, /a/info/, /old/, /gone/
    assert stats.excluded_by_rule == {"^/b/": 1}
    assert stats.page_types["product"] == 1 and stats.domains["car"] == 1
    assert set(site.paths_requested()) == {"/robots.txt", "/sitemap.xml"}


def test_refusal_before_streak_ends_is_recorded_as_failure(
    container: Container, site: FakeSite
) -> None:
    original = site.handler

    def refuse_b(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403) if request.url.path == "/b/" else original(request)

    from tests.fakes import FakeTime, make_fetcher

    container.__dict__["fetcher"] = make_fetcher(refuse_b, FakeTime())
    container.__dict__.pop("stages", None)
    final = run(container).run
    assert final.status == RunStatus.COMPLETED
    assert page(container, "/b/").status == PageStatus.FAILED
    assert page(container, "/b/").last_error == "HTTP 403"
