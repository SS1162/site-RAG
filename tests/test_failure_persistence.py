"""Failure policy applied through the repositories."""

from dataclasses import replace

from siterag.domain.enums import EventType, PageStatus, RunStatus
from siterag.domain.failure_policy import record_failure, record_success
from tests.conftest import FakeClock, UowFactory, discover, start_run
from tests.test_chunks import build_chunks


def fail_in_new_run(make_uow: UowFactory, clock: FakeClock, url_id: str, version: str = "1") -> str:
    clock.advance(hours=1)
    with make_uow() as uow:
        run = start_run(uow)
        page = uow.pages.get(url_id)
        assert page is not None
        page, event = record_failure(
            page,
            run_id=run.run_id,
            stage="fetch",
            stage_version=version,
            error="timeout",
            now=clock.now,
            max_consecutive_failed_runs=3,
        )
        uow.pages.update(page)
        if event is not None:
            uow.events.add(run.run_id, url_id, event, stage="fetch")
        uow.runs.finish(run.run_id, RunStatus.COMPLETED)
        uow.commit()
    return run.run_id


def test_indexed_page_that_fails_keeps_chunks_and_receipt(
    make_uow: UowFactory, clock: FakeClock
) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        uow.chunks.replace_for_page(page.url_id, build_chunks(page.url_id, ["a", "b"]))
        uow.pages.update(replace(page, chunks_hash="ch", status=PageStatus.INDEXED))
        uow.receipts.upsert(page.url_id, "index", "ch", "bge-m3:1", run.run_id)
        uow.runs.finish(run.run_id, RunStatus.COMPLETED)
        uow.commit()

    for _ in range(3):
        last_run = fail_in_new_run(make_uow, clock, page.url_id)

    with make_uow() as uow:
        stored = uow.pages.get(page.url_id)
        assert stored is not None
        assert stored.status == PageStatus.FAILED_PERMANENT and stored.fail_count == 3
        assert uow.events.summary(last_run) == {EventType.FAILED_PERMANENT: 1}
        assert len(uow.chunks.list_by_page(page.url_id)) == 2
        assert uow.receipts.get(page.url_id, "index") is not None
        assert uow.pages.list_pending_unindex() == []


def test_reset_for_version_change_makes_page_eligible(
    make_uow: UowFactory, clock: FakeClock
) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        uow.pages.update(replace(page, raw_hash="h"))
        uow.runs.finish(run.run_id, RunStatus.COMPLETED)
        uow.commit()
    for _ in range(3):
        fail_in_new_run(make_uow, clock, page.url_id)

    clock.advance(hours=1)
    with make_uow() as uow:
        run = start_run(uow)
        assert list(uow.pages.iter_needing_stage("extract", "1")) == []
        assert uow.pages.reset_failures_for_version_change("fetch", "1", run.run_id) == []
        reset = uow.pages.reset_failures_for_version_change("fetch", "2", run.run_id)
        assert [p.url_id for p in reset] == [page.url_id]
        uow.commit()

    with make_uow() as uow:
        stored = uow.pages.get(page.url_id)
        assert stored is not None
        assert stored.status == PageStatus.FAILED and stored.fail_count == 0
        assert stored.last_error == "timeout"
        assert [p.url_id for p in uow.pages.iter_needing_stage("extract", "1")] == [page.url_id]
        (event,) = uow.events.list(run.run_id)
        assert event.event == EventType.RESTORED and event.stage == "fetch"
        assert event.details["reason"] == "stage_version_changed"


def test_success_clears_failure(make_uow: UowFactory, clock: FakeClock) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = discover(uow, "/a", run.run_id)
        uow.runs.finish(run.run_id, RunStatus.COMPLETED)
        uow.commit()
    fail_in_new_run(make_uow, clock, page.url_id)
    with make_uow() as uow:
        failed = uow.pages.get(page.url_id)
        assert failed is not None and failed.fail_count == 1
        uow.pages.update(replace(record_success(failed), status=PageStatus.FETCHED))
        uow.commit()
    with make_uow() as uow:
        stored = uow.pages.get(page.url_id)
        assert stored is not None
        assert (stored.fail_count, stored.failed_stage, stored.last_error) == (0, None, None)
