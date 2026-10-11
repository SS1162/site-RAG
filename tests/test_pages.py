from dataclasses import replace
from datetime import UTC, datetime

from siterag.domain.enums import DiscoveredVia, EventType, PageStatus, RunStatus
from siterag.domain.urls import normalize_url
from tests.conftest import RULES, FakeClock, UowFactory, discover, start_run


def test_upsert_new_then_existing(make_uow: UowFactory, clock: FakeClock) -> None:
    url = normalize_url("https://x.co.il/ביטוח-דירה", RULES)
    lastmod = datetime(2026, 1, 1, tzinfo=UTC)
    with make_uow() as uow:
        run1 = start_run(uow)
        seed, _ = uow.pages.upsert_discovered(
            normalize_url("https://x.co.il/", RULES), DiscoveredVia.SEED, None, None, run1.run_id
        )
        page, is_new = uow.pages.upsert_discovered(
            url, DiscoveredVia.SITEMAP, seed.url_id, lastmod, run1.run_id
        )
        assert is_new
        assert page.status == PageStatus.DISCOVERED
        assert page.url_display == "https://x.co.il/ביטוח-דירה"
        assert page.found_on == seed.url_id and page.sitemap_lastmod == lastmod
        uow.runs.finish(run1.run_id, RunStatus.COMPLETED)
        uow.commit()

    clock.advance(days=1)
    with make_uow() as uow:
        run2 = start_run(uow)
        again, is_new = uow.pages.upsert_discovered(url, DiscoveredVia.LINK, None, None, run2.run_id)
        uow.commit()
    assert not is_new
    assert again.last_seen_run_id == run2.run_id and again.last_seen_at == clock.now
    assert again.first_seen_at < again.last_seen_at
    assert again.discovered_via == DiscoveredVia.SITEMAP  # first discovery wins
    assert again.found_on == seed.url_id
    assert again.sitemap_lastmod == lastmod


def test_restore_removed_page(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        removed = uow.pages.mark_removed(page.url_id, run.run_id)
        assert removed.status == PageStatus.REMOVED and removed.removed_at is not None
        restored = discover(uow, "/a", run.run_id)
        assert restored.status == PageStatus.DISCOVERED and restored.removed_at is None
        events = [e.event for e in uow.events.list(run.run_id)]
        assert events == [EventType.REMOVED, EventType.RESTORED]


def test_get_and_update_round_trip(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        uow.pages.update(
            replace(page, title="דירה", extra={"breadcrumbs": ["ראשי", "דירה"]}, http_status=200)
        )
        uow.commit()
    with make_uow() as uow:
        stored = uow.pages.get_by_url(page.url)
    assert stored is not None
    assert stored.title == "דירה" and stored.http_status == 200
    assert stored.extra == {"breadcrumbs": ["ראשי", "דירה"]}


def test_mark_redirected_and_duplicate(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        a, b, c = (discover(uow, p, run.run_id) for p in ("/a", "/b", "/c"))
        assert uow.pages.mark_redirected(a.url_id, b.url_id, run.run_id).redirected_to == b.url_id
        assert uow.pages.mark_duplicate(c.url_id, b.url_id, run.run_id).duplicate_of == b.url_id
        assert uow.pages.count_active() == 1
        summary = uow.events.summary(run.run_id)
        assert summary == {EventType.REDIRECTED: 1, EventType.DUPLICATE: 1}
        assert uow.pages.count_by_status() == {
            PageStatus.DISCOVERED: 1,
            PageStatus.REDIRECTED: 1,
            PageStatus.DUPLICATE: 1,
        }


def test_list_removal_candidates(make_uow: UowFactory, clock: FakeClock) -> None:
    with make_uow() as uow:
        run1 = start_run(uow)
        a, b, gone = (discover(uow, p, run1.run_id) for p in ("/a", "/b", "/gone"))
        uow.pages.mark_removed(gone.url_id, run1.run_id)
        uow.runs.finish(run1.run_id, RunStatus.COMPLETED)
        uow.commit()
    clock.advance(hours=1)
    with make_uow() as uow:
        run2 = start_run(uow)
        discover(uow, "/a", run2.run_id)
        candidates = uow.pages.list_removal_candidates(run2.run_id)
    assert [p.url_id for p in candidates] == [b.url_id]


def test_mark_methods_record_details(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        a, b, c, d = (discover(uow, p, run.run_id) for p in ("/a", "/b", "/c", "/d"))
        uow.pages.mark_removed(a.url_id, run.run_id, {"reason": "out_of_scope"})
        uow.pages.mark_redirected(b.url_id, d.url_id, run.run_id, {"http_status": 301})
        uow.pages.mark_duplicate(c.url_id, d.url_id, run.run_id, {"by": "hash"})
        details = [e.details for e in uow.events.list(run.run_id)]
    assert details == [
        {"reason": "out_of_scope"},
        {"target": d.url_id, "http_status": 301},
        {"of": d.url_id, "by": "hash"},
    ]


def test_skipped_is_inactive_and_pending_unindex(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        uow.pages.update(replace(page, status=PageStatus.SKIPPED))
        uow.receipts.upsert(page.url_id, "index", "h", "1", run.run_id)
        assert uow.pages.count_active() == 0
        assert [p.url_id for p in uow.pages.list_pending_unindex()] == [page.url_id]
