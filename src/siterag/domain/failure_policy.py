"""Across-run failure policy. Pure functions; callers persist the returned pages.

Within-run retries belong to the fetcher. Here a failure is counted at most once per run,
a page becomes ``failed_permanent`` after too many consecutive failed runs, and a change
in the version of the failed stage (i.e. the code was fixed) makes it eligible again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from siterag.domain.entities import Page
from siterag.domain.enums import EventType, PageStatus


def record_failure(
    page: Page,
    *,
    run_id: str,
    stage: str,
    stage_version: str,
    error: str,
    now: datetime,
    max_consecutive_failed_runs: int,
) -> tuple[Page, EventType | None]:
    """Return the page updated for a failure and the event to record (None if none).

    A second failure in the same run only refreshes the error details. If the previous
    failure was in the same stage but under a different stage version, counting restarts.
    """
    if page.last_failed_run_id == run_id:
        updated = replace(
            page,
            failed_stage=stage,
            failed_stage_version=stage_version,
            last_error=error,
            last_error_at=now,
        )
        return updated, None

    count = page.fail_count
    if page.failed_stage == stage and page.failed_stage_version != stage_version:
        count = 0
    count += 1

    permanent = count >= max_consecutive_failed_runs
    updated = replace(
        page,
        status=PageStatus.FAILED_PERMANENT if permanent else PageStatus.FAILED,
        fail_count=count,
        failed_stage=stage,
        failed_stage_version=stage_version,
        last_failed_run_id=run_id,
        last_error=error,
        last_error_at=now,
    )
    return updated, EventType.FAILED_PERMANENT if permanent else EventType.FAILED


def record_success(page: Page) -> Page:
    """Return the page with its failure state cleared. The caller sets the new status."""
    return replace(
        page,
        fail_count=0,
        failed_stage=None,
        failed_stage_version=None,
        last_error=None,
        last_error_at=None,
    )


def needs_failure_reset(page: Page, stage: str, current_version: str) -> bool:
    """True when the page failed in ``stage`` under a version that is no longer current."""
    return (
        page.fail_count > 0
        and page.failed_stage == stage
        and page.failed_stage_version != current_version
    )


def reset_failure(page: Page) -> Page:
    """Return the page made eligible again after a stage version change.

    Status becomes ``failed`` (active), the count restarts; ``last_error`` is kept for history.
    """
    return replace(
        page,
        status=PageStatus.FAILED,
        fail_count=0,
        failed_stage=None,
        failed_stage_version=None,
    )
