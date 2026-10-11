from datetime import UTC, datetime

from siterag.domain.entities import Page
from siterag.domain.enums import DiscoveredVia, EventType, PageStatus
from siterag.domain.failure_policy import (
    needs_failure_reset,
    record_failure,
    record_success,
    reset_failure,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def make_page() -> Page:
    return Page(
        url_id="a" * 16,
        url="https://x.co.il/a",
        url_display="https://x.co.il/a",
        status=PageStatus.INDEXED,
        discovered_via=DiscoveredVia.SEED,
        first_seen_at=NOW,
        last_seen_at=NOW,
        last_seen_run_id="r0",
    )


def fail(page: Page, run_id: str, version: str = "1") -> tuple[Page, EventType | None]:
    return record_failure(
        page,
        run_id=run_id,
        stage="fetch",
        stage_version=version,
        error="boom",
        now=NOW,
        max_consecutive_failed_runs=3,
    )


def test_increments_once_per_run() -> None:
    page, event = fail(make_page(), "r1")
    assert (page.fail_count, page.status, event) == (1, PageStatus.FAILED, EventType.FAILED)
    page, event = fail(page, "r1")
    assert page.fail_count == 1 and event is None


def test_failed_permanent_after_three_runs() -> None:
    page = make_page()
    for run_id in ("r1", "r2"):
        page, _ = fail(page, run_id)
    page, event = fail(page, "r3")
    assert page.status == PageStatus.FAILED_PERMANENT
    assert event == EventType.FAILED_PERMANENT
    assert not page.is_active


def test_new_failure_under_new_version_restarts_count() -> None:
    page, _ = fail(make_page(), "r1", "1")
    page, _ = fail(page, "r2", "1")
    page, _ = fail(page, "r3", "2")
    assert page.fail_count == 1 and page.status == PageStatus.FAILED


def test_reset_on_version_bump() -> None:
    page = make_page()
    for run_id in ("r1", "r2", "r3"):
        page, _ = fail(page, run_id)
    assert not needs_failure_reset(page, "fetch", "1")
    assert not needs_failure_reset(page, "extract", "2")
    assert needs_failure_reset(page, "fetch", "2")
    page = reset_failure(page)
    assert page.status == PageStatus.FAILED and page.is_active
    assert page.fail_count == 0
    assert page.last_error == "boom"


def test_success_clears_failure_state() -> None:
    page, _ = fail(make_page(), "r1")
    page = record_success(page)
    assert page.fail_count == 0
    assert page.failed_stage is None and page.failed_stage_version is None
    assert page.last_error is None
