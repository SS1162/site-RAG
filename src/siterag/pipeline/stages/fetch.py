"""Fetch stage: download every page seen in the run, follow links, then the removal phase.

Each page is handled in its own transaction, so an interrupted run keeps its progress.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from siterag.domain.entities import Page
from siterag.domain.enums import ContentType, DiscoveredVia, EventType, PageStatus
from siterag.domain.errors import RemovalThresholdExceededError
from siterag.domain.failure_policy import record_failure, record_success
from siterag.domain.hashing import sha256_hex
from siterag.domain.safety import check_removals
from siterag.domain.scope import decoded_path, has_extension
from siterag.domain.urls import normalize_url
from siterag.ingest.links import extract_links
from siterag.pipeline.context import RunContext
from siterag.pipeline.stages.discover import load_robots_into
from siterag.ports.fetcher import Fetcher, FetchResult, RobotsRules, SkipReason
from siterag.ports.stage import StageResult
from siterag.ports.unit_of_work import UnitOfWork
from siterag.storage.raw_store import RawStore

STAGE = "fetch"
REMOVED_STATUSES = frozenset({404, 410})
REFUSAL_STATUSES = frozenset({403, 429})
"""Final statuses whose failure is held back while the circuit breaker may still trip."""
CONTENT_COUNTERS = {
    EventType.NEW: "pages_new",
    EventType.CHANGED: "pages_changed",
    EventType.UNCHANGED: "pages_unchanged",
    EventType.ORPHAN: "pages_orphaned",
}
BECOME_FETCHED = frozenset(
    {PageStatus.DISCOVERED, PageStatus.FAILED, PageStatus.REDIRECTED, PageStatus.SKIPPED}
)
"""Statuses replaced by ``fetched`` after any successful fetch (others are kept)."""
PROGRESS_EVERY = 50


class FetchStage:
    """Downloads pages (conditional requests), stores raw bytes and follows links."""

    name = STAGE

    def __init__(
        self,
        fetcher: Fetcher,
        raw_store: RawStore,
        robots_loader: Callable[[], RobotsRules],
    ) -> None:
        self._fetcher = fetcher
        self._raw = raw_store
        self._robots_loader = robots_loader
        self._refused: list[tuple[str, int]] = []
        """(url_id, status) of pages refused with 429/403, not yet recorded as failed."""

    # -- main loop -----------------------------------------------------------------

    def run(self, ctx: RunContext) -> StageResult:
        """Fetch the queue, then run the removal phase when allowed."""
        load_robots_into(ctx, self._fetcher, self._robots_loader)
        frontier = ctx.frontier
        if not ctx.flags.discovery_ran:
            with ctx.uow_factory() as uow:  # nothing discovered in this run: all active pages
                for page in uow.pages.list_removal_candidates(ctx.run_id):
                    frontier.enqueue(page)

        self._refused = []
        requests = 0
        while (url_id := frontier.next()) is not None:
            if ctx.flags.limit is not None and requests >= ctx.flags.limit:
                ctx.warn(f"--limit {ctx.flags.limit} reached: remaining queue not fetched")
                break
            with ctx.uow_factory() as uow:
                page = uow.pages.get(url_id)
                if page is None:
                    continue
                requests += self._fetch_page(ctx, uow, page, verification=False)
                uow.commit()
            if requests and requests % PROGRESS_EVERY == 0:
                ctx.logger.info("Fetched %d pages, %d admitted", requests,
                                frontier.stats.discovered)
        self._record_refusals(ctx)

        if ctx.flags.limit is not None:
            ctx.warn("Removal phase disabled: --limit was used")
        elif not ctx.flags.discovery_ran:
            ctx.warn("Removal phase disabled: discovery did not run in this run")
        else:
            self._removal_phase(ctx)
        return StageResult(self.name, {"requests": requests})

    def _fetch_page(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, *, verification: bool
    ) -> bool:
        """Fetch one page and record the outcome; return True when a request was made."""
        ctx.frontier.mark_done(page.url_id)
        if has_extension(decoded_path(page.url), ctx.site.scope.skip_extensions):
            self._skip(ctx, uow, page, SkipReason.EXTENSION)
            return False
        conditional = not verification and self._raw.exists(page.url_id, page.content_type)
        result = self._fetcher.fetch(
            page.url,
            etag=page.etag if conditional else None,
            last_modified=page.last_modified if conditional else None,
        )
        if result.status is not None and result.status < 400:
            self._record_refusals(ctx, uow)  # the refusal streak is over
        self._apply(ctx, uow, page, result, verification=verification)
        return True

    def _record_refusals(self, ctx: RunContext, uow: UnitOfWork | None = None) -> None:
        """Record held-back 429/403 failures (failure policy) once the streak has ended.

        If the circuit breaker trips first, the run aborts and they are never recorded:
        no page is marked failed because the server was refusing everything.
        """
        if not self._refused:
            return
        if uow is None:
            with ctx.uow_factory() as own:
                self._record_refusals(ctx, own)
                own.commit()
            return
        for url_id, status in self._refused:
            page = uow.pages.get(url_id)
            if page is not None:
                self._fail(ctx, uow, page, f"HTTP {status}", status)
        self._refused = []

    # -- outcome handling ----------------------------------------------------------

    def _apply(
        self,
        ctx: RunContext,
        uow: UnitOfWork,
        page: Page,
        result: FetchResult,
        *,
        verification: bool,
    ) -> None:
        """Record a fetch result. ``verification``: removal-phase check (200 → orphan)."""
        if result.error is not None:
            self._fail(ctx, uow, page, result.error, None)
            return
        if result.skip_reason == SkipReason.OFFSITE_REDIRECT:
            self._skip(ctx, uow, page, result.skip_reason, {"target": result.final_url})
            return

        target = self._follow_redirect(ctx, uow, page, result)
        if target is None:
            return
        page = target

        if result.status in REMOVED_STATUSES:
            uow.pages.mark_removed(page.url_id, ctx.run_id, {"http_status": result.status})
            uow.runs.increment(ctx.run_id, pages_removed=1)
        elif result.skip_reason is not None:
            self._skip(ctx, uow, page, result.skip_reason)
        elif result.status == 304:
            self._not_modified(ctx, uow, page, result)
        elif result.status == 200 and result.content is not None:
            self._success(ctx, uow, page, result, verification=verification)
        elif result.status in REFUSAL_STATUSES:
            assert result.status is not None
            self._refused.append((page.url_id, result.status))
        else:
            self._fail(ctx, uow, page, f"HTTP {result.status}", result.status)

    def _follow_redirect(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, result: FetchResult
    ) -> Page | None:
        """Return the page that owns the content: ``page`` itself, or the redirect target.

        Returns None when there is nothing more to do (target out of scope, or already
        handled in this run).
        """
        if result.final_url == result.requested_url:
            return page
        frontier = ctx.frontier
        target_url = normalize_url(result.final_url, frontier.url_rules)
        if target_url.url_id == page.url_id:
            return page
        reason = frontier.exclusion(target_url.url)
        if reason is not None:
            self._skip(ctx, uow, page, SkipReason.OUT_OF_SCOPE_REDIRECT,
                       {"target": target_url.url_display, "rule": reason})
            return None
        target = frontier.admit(
            uow, target_url, DiscoveredVia.LINK, page.url_id, frontier.depth_of(page), force=True
        ) or uow.pages.get(target_url.url_id)
        assert target is not None
        if page.status != PageStatus.REDIRECTED or page.redirected_to != target.url_id:
            uow.pages.mark_redirected(
                page.url_id, target.url_id, ctx.run_id, {"http_status": result.redirect_status}
            )
            uow.runs.increment(ctx.run_id, pages_redirected=1)
        if frontier.is_done(target.url_id):
            return None
        frontier.mark_done(target.url_id)
        return target

    def _success(
        self,
        ctx: RunContext,
        uow: UnitOfWork,
        page: Page,
        result: FetchResult,
        *,
        verification: bool,
    ) -> None:
        content = result.content
        content_type = result.content_type
        assert content is not None and content_type is not None
        raw_hash = sha256_hex(content)
        if verification:
            event = EventType.ORPHAN
        elif page.raw_hash is None:
            event = EventType.NEW
        elif raw_hash != page.raw_hash:
            event = EventType.CHANGED
        else:
            event = EventType.UNCHANGED
        if raw_hash != page.raw_hash or not self._raw.exists(page.url_id, content_type):
            self._raw.save(page.url_id, content_type, content)

        updated = replace(
            self._fetched(ctx, uow, page, event, result),
            content_type=content_type,
            raw_hash=raw_hash,
        )
        uow.pages.update(updated)
        details: dict[str, Any] = {"changed": raw_hash != page.raw_hash} if verification else {}
        self._content_event(ctx, uow, page, event, details)
        if content_type == ContentType.HTML and not verification:
            self._follow_links(ctx, uow, updated, content)

    def _not_modified(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, result: FetchResult
    ) -> None:
        """304: unchanged. Links are re-read from the stored file so its children stay seen."""
        if not self._raw.exists(page.url_id, page.content_type):
            # Only possible after a redirect to a page whose file is gone: refetch plainly.
            retry = self._fetcher.fetch(page.url)
            self._apply(ctx, uow, page, retry, verification=False)
            return
        updated = self._fetched(ctx, uow, page, EventType.UNCHANGED, result)
        uow.pages.update(updated)
        self._content_event(ctx, uow, page, EventType.UNCHANGED, {"http_status": 304})
        if page.content_type == ContentType.HTML:
            content = self._raw.read(page.url_id, ContentType.HTML)
            self._follow_links(ctx, uow, updated, content)

    def _fetched(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, event: EventType, result: FetchResult
    ) -> Page:
        """Page fields common to every successful fetch (200 or 304)."""
        headers = result.headers
        status = page.status
        if event in (EventType.NEW, EventType.CHANGED) or status in BECOME_FETCHED:
            status = PageStatus.FETCHED
        if page.status in (PageStatus.REDIRECTED, PageStatus.SKIPPED):
            uow.events.add(ctx.run_id, page.url_id, EventType.RESTORED, STAGE,
                           {"from": page.status.value})
        return replace(
            record_success(page),
            status=status,
            redirected_to=None,
            http_status=result.status,
            etag=headers.get("etag", page.etag),
            last_modified=headers.get("last-modified", page.last_modified),
            last_fetch_ok_at=ctx.clock(),
            last_fetch_ok_run_id=ctx.run_id,
        )

    def _content_event(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, event: EventType,
        details: dict[str, Any],
    ) -> None:
        uow.events.add(ctx.run_id, page.url_id, event, STAGE, details)
        uow.runs.increment(ctx.run_id, **{CONTENT_COUNTERS[event]: 1})

    def _follow_links(self, ctx: RunContext, uow: UnitOfWork, page: Page, content: bytes) -> None:
        frontier = ctx.frontier
        depth = frontier.depth_of(page) + 1
        for link in extract_links(content, page.url, frontier.url_rules, frontier.site_host):
            frontier.admit(uow, link.url, DiscoveredVia.LINK, page.url_id, depth,
                           is_pdf=link.is_pdf)

    def _skip(
        self,
        ctx: RunContext,
        uow: UnitOfWork,
        page: Page,
        reason: SkipReason,
        extra: dict[str, Any] | None = None,
    ) -> None:
        uow.pages.update(replace(page, status=PageStatus.SKIPPED))
        uow.events.add(ctx.run_id, page.url_id, EventType.SKIPPED, STAGE,
                       {"reason": reason.value, **(extra or {})})
        uow.runs.increment(ctx.run_id, pages_skipped=1)

    def _fail(
        self, ctx: RunContext, uow: UnitOfWork, page: Page, error: str, status: int | None
    ) -> None:
        """Across-run failure policy; the page keeps its chunks and index receipt."""
        updated, event = record_failure(
            page,
            run_id=ctx.run_id,
            stage=STAGE,
            stage_version=ctx.stage_version(STAGE),
            error=error,
            now=ctx.clock(),
            max_consecutive_failed_runs=ctx.site.failure.max_consecutive_failed_runs,
        )
        uow.pages.update(replace(updated, http_status=status))
        if event is not None:
            uow.events.add(ctx.run_id, page.url_id, event, STAGE,
                           {"error": error, "http_status": status})
            uow.runs.increment(ctx.run_id, pages_failed=1)

    # -- removal phase -------------------------------------------------------------

    def _removal_phase(self, ctx: RunContext) -> None:
        """Remove active pages not seen in this run, after the safety guard and verification."""
        frontier = ctx.frontier
        with ctx.uow_factory() as uow:
            candidates = uow.pages.list_removal_candidates(ctx.run_id)
            active = uow.pages.count_active()
        if not candidates:
            return
        excluded = {p.url_id: frontier.exclusion(p.url) for p in candidates}
        out_of_scope = [p for p in candidates if excluded[p.url_id] is not None]
        to_verify = [p for p in candidates if excluded[p.url_id] is None]
        ctx.logger.info("Removal phase: %d candidates (%d out of scope) of %d active pages",
                        len(candidates), len(out_of_scope), active)

        if ctx.flags.allow_mass_removal:
            ctx.warn("--allow-mass-removal: removal threshold not applied")
        else:
            try:
                check_removals(len(candidates), active, ctx.site.safety.removal_max_ratio)
            except RemovalThresholdExceededError as exc:
                ctx.warn(f"Removal phase skipped, nothing removed: {exc}")
                return

        with ctx.uow_factory() as uow:
            for page in out_of_scope:
                uow.pages.mark_removed(page.url_id, ctx.run_id,
                                       {"reason": "out_of_scope", "rule": excluded[page.url_id]})
                uow.runs.increment(ctx.run_id, pages_removed=1)
            uow.commit()

        for candidate in to_verify:
            with ctx.uow_factory() as uow:
                page = uow.pages.get(candidate.url_id)
                if page is None or not page.is_active or ctx.frontier.is_done(page.url_id):
                    continue
                self._fetch_page(ctx, uow, page, verification=True)
                uow.commit()
        self._record_refusals(ctx)

