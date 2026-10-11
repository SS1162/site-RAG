"""SQLAlchemy implementations of the repository ports."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from siterag.adapters.sqlalchemy.mappers import (
    chunk_to_domain,
    chunk_to_model,
    event_to_domain,
    page_to_domain,
    page_to_model,
    receipt_to_domain,
    run_to_domain,
)
from siterag.adapters.sqlalchemy.models import (
    ChunkModel,
    PageEventModel,
    PageModel,
    RunModel,
    StageReceiptModel,
)
from siterag.domain.entities import (
    RUN_COUNTERS,
    Chunk,
    Page,
    PageEvent,
    Run,
    StageReceipt,
)
from siterag.domain.enums import (
    INACTIVE_STATUSES,
    UNINDEX_STATUSES,
    DiscoveredVia,
    EventType,
    PageStatus,
    RunStatus,
)
from siterag.domain.errors import ConfigError, NotFoundError, RunLockedError
from siterag.domain.failure_policy import needs_failure_reset, reset_failure
from siterag.domain.urls import NormalizedUrl

Clock = Callable[[], datetime]

INDEX_STAGE = "index"
"""Stage whose receipt means "this page is in the search index"."""


def validate_stage_inputs(stage_inputs: Mapping[str, str]) -> None:
    """Raise ``ConfigError`` unless every configured input column exists on ``pages``."""
    columns = set(PageModel.__table__.columns.keys())
    unknown = {stage: col for stage, col in stage_inputs.items() if col not in columns}
    if unknown:
        pairs = ", ".join(f"{stage}: {col}" for stage, col in sorted(unknown.items()))
        raise ConfigError(f"pipeline.stage_inputs refers to unknown pages columns: {pairs}")


def _add_event(
    session: Session,
    clock: Clock,
    run_id: str,
    url_id: str,
    event: EventType,
    stage: str | None = None,
    details: dict[str, Any] | None = None,
) -> PageEventModel:
    model = PageEventModel(
        run_id=run_id,
        url_id=url_id,
        event=event,
        stage=stage,
        details=dict(details or {}),
        created_at=clock(),
    )
    session.add(model)
    session.flush()
    return model


class SqlAlchemyRunRepository:
    """Runs and the run lock."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def _require(self, run_id: str) -> RunModel:
        model = self._session.get(RunModel, run_id)
        if model is None:
            raise NotFoundError(f"Run not found: {run_id}")
        return model

    def start(
        self,
        site_id: str,
        command: str,
        args: dict[str, Any],
        config_hash: str,
        site_profile_hash: str,
        stale_after_hours: float = 12,
    ) -> Run:
        """Create a ``running`` run after checking the run lock."""
        now = self._clock()
        cutoff = now - timedelta(hours=stale_after_hours)
        running = self._session.scalars(
            select(RunModel).where(RunModel.status == RunStatus.RUNNING)
        ).all()
        for other in running:
            if other.started_at > cutoff:
                raise RunLockedError(
                    f"Run {other.run_id} ({other.command}) is still running since "
                    f"{other.started_at.isoformat()}"
                )
            other.status = RunStatus.ABORTED
            other.finished_at = now
            other.abort_reason = "stale"

        model = RunModel(
            run_id=str(uuid.uuid4()),
            site_id=site_id,
            status=RunStatus.RUNNING,
            started_at=now,
            command=command,
            args=dict(args),
            config_hash=config_hash,
            site_profile_hash=site_profile_hash,
        )
        self._session.add(model)
        self._session.flush()
        return run_to_domain(model)

    def finish(self, run_id: str, status: RunStatus, abort_reason: str | None = None) -> Run:
        """Set the final status of a run."""
        model = self._require(run_id)
        model.status = status
        model.finished_at = self._clock()
        model.abort_reason = abort_reason
        self._session.flush()
        return run_to_domain(model)

    def increment(self, run_id: str, **counters: int) -> None:
        """Atomically add to the run's page counters."""
        unknown = set(counters) - set(RUN_COUNTERS)
        if unknown:
            raise ValueError(f"Unknown run counters: {', '.join(sorted(unknown))}")
        if not counters:
            return
        self._require(run_id)
        values = {name: getattr(RunModel, name) + amount for name, amount in counters.items()}
        self._session.execute(
            update(RunModel)
            .where(RunModel.run_id == run_id)
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )

    def get(self, run_id: str) -> Run | None:
        """Return a run by id, or None."""
        model = self._session.get(RunModel, run_id)
        return run_to_domain(model) if model else None

    def get_last(self) -> Run | None:
        """Return the most recently started run, or None."""
        model = self._session.scalars(
            select(RunModel).order_by(RunModel.started_at.desc()).limit(1)
        ).first()
        return run_to_domain(model) if model else None


class SqlAlchemyPageRepository:
    """Pages and receipt-based work selection."""

    def __init__(self, session: Session, clock: Clock, stage_inputs: Mapping[str, str]) -> None:
        self._session = session
        self._clock = clock
        self._stage_inputs = dict(stage_inputs)

    def _require(self, url_id: str) -> PageModel:
        model = self._session.get(PageModel, url_id)
        if model is None:
            raise NotFoundError(f"Page not found: {url_id}")
        return model

    def get(self, url_id: str) -> Page | None:
        """Return a page by id, or None."""
        model = self._session.get(PageModel, url_id)
        return page_to_domain(model) if model else None

    def get_by_url(self, url: str) -> Page | None:
        """Return a page by its normalized URL, or None."""
        model = self._session.scalars(select(PageModel).where(PageModel.url == url)).first()
        return page_to_domain(model) if model else None

    def upsert_discovered(
        self,
        normalized_url: NormalizedUrl,
        discovered_via: DiscoveredVia,
        found_on: str | None,
        sitemap_lastmod: datetime | None,
        run_id: str,
    ) -> tuple[Page, bool]:
        """Insert a new page or refresh an existing one; restore it if it was removed."""
        now = self._clock()
        model = self._session.get(PageModel, normalized_url.url_id)
        if model is None:
            model = PageModel(
                url_id=normalized_url.url_id,
                url=normalized_url.url,
                url_display=normalized_url.url_display,
                status=PageStatus.DISCOVERED,
                discovered_via=discovered_via,
                found_on=found_on,
                sitemap_lastmod=sitemap_lastmod,
                first_seen_at=now,
                last_seen_at=now,
                last_seen_run_id=run_id,
                extra={},
                fail_count=0,
            )
            self._session.add(model)
            self._session.flush()
            return page_to_domain(model), True

        model.last_seen_at = now
        model.last_seen_run_id = run_id
        if sitemap_lastmod is not None:
            model.sitemap_lastmod = sitemap_lastmod
        if model.found_on is None and found_on is not None:
            model.found_on = found_on  # keep the *first* referring page
        if model.status == PageStatus.REMOVED:
            model.status = PageStatus.DISCOVERED
            model.removed_at = None
            _add_event(self._session, self._clock, run_id, model.url_id, EventType.RESTORED)
        self._session.flush()
        return page_to_domain(model), False

    def update(self, page: Page) -> None:
        """Persist all fields of an existing page."""
        page_to_model(page, self._require(page.url_id))
        self._session.flush()

    def _mark(
        self,
        url_id: str,
        run_id: str,
        event: EventType,
        details: dict[str, Any] | None = None,
        **changes: Any,
    ) -> Page:
        model = self._require(url_id)
        for name, value in changes.items():
            setattr(model, name, value)
        _add_event(self._session, self._clock, run_id, url_id, event, details=details)
        return page_to_domain(model)

    def mark_removed(
        self, url_id: str, run_id: str, details: dict[str, Any] | None = None
    ) -> Page:
        """Mark a page removed. Chunks and receipts stay until the unindex step."""
        return self._mark(
            url_id,
            run_id,
            EventType.REMOVED,
            details,
            status=PageStatus.REMOVED,
            removed_at=self._clock(),
        )

    def mark_redirected(
        self, url_id: str, target_url_id: str, run_id: str, details: dict[str, Any] | None = None
    ) -> Page:
        """Mark a page as redirecting to ``target_url_id``."""
        return self._mark(
            url_id,
            run_id,
            EventType.REDIRECTED,
            {"target": target_url_id, **(details or {})},
            status=PageStatus.REDIRECTED,
            redirected_to=target_url_id,
        )

    def mark_duplicate(
        self, url_id: str, of_url_id: str, run_id: str, details: dict[str, Any] | None = None
    ) -> Page:
        """Mark a page as a duplicate of ``of_url_id``."""
        return self._mark(
            url_id,
            run_id,
            EventType.DUPLICATE,
            {"of": of_url_id, **(details or {})},
            status=PageStatus.DUPLICATE,
            duplicate_of=of_url_id,
        )

    def list_removal_candidates(self, run_id: str) -> list[Page]:
        """Active pages that were not seen in ``run_id``."""
        models = self._session.scalars(
            select(PageModel)
            .where(
                PageModel.status.not_in(INACTIVE_STATUSES),
                PageModel.last_seen_run_id != run_id,
            )
            .order_by(PageModel.url_id)
        )
        return [page_to_domain(m) for m in models]

    def iter_needing_stage(
        self, stage: str, stage_version: str, limit: int | None = None
    ) -> Iterator[Page]:
        """Active pages with an input hash whose receipt is missing or out of date.

        Results are loaded up front so the caller may update pages while iterating.
        """
        if stage not in self._stage_inputs:
            raise ValueError(f"Stage {stage!r} has no input column in pipeline.stage_inputs")
        input_hash = getattr(PageModel, self._stage_inputs[stage])
        receipt = StageReceiptModel
        stmt = (
            select(PageModel)
            .outerjoin(
                receipt,
                and_(receipt.url_id == PageModel.url_id, receipt.stage == stage),
            )
            .where(
                PageModel.status.not_in(INACTIVE_STATUSES),
                input_hash.is_not(None),
                or_(
                    receipt.url_id.is_(None),
                    receipt.input_hash != input_hash,
                    receipt.stage_version != stage_version,
                ),
            )
            .order_by(PageModel.url_id)
            .limit(limit)
        )
        pages = [page_to_domain(m) for m in self._session.scalars(stmt)]
        return iter(pages)

    def list_pending_unindex(self) -> list[Page]:
        """Removed/redirected/duplicate pages that still have an index receipt."""
        stmt = (
            select(PageModel)
            .join(
                StageReceiptModel,
                and_(
                    StageReceiptModel.url_id == PageModel.url_id,
                    StageReceiptModel.stage == INDEX_STAGE,
                ),
            )
            .where(PageModel.status.in_(UNINDEX_STATUSES))
            .order_by(PageModel.url_id)
        )
        return [page_to_domain(m) for m in self._session.scalars(stmt)]

    def reset_failures_for_version_change(
        self, stage: str, stage_version: str, run_id: str
    ) -> list[Page]:
        """Reset pages that failed in ``stage`` under an older version; record an event each."""
        models = self._session.scalars(
            select(PageModel).where(PageModel.failed_stage == stage, PageModel.fail_count > 0)
        ).all()
        reset: list[Page] = []
        for model in models:
            page = page_to_domain(model)
            if not needs_failure_reset(page, stage, stage_version):
                continue
            old_version = page.failed_stage_version
            page = reset_failure(page)
            page_to_model(page, model)
            _add_event(
                self._session,
                self._clock,
                run_id,
                page.url_id,
                EventType.RESTORED,
                stage=stage,
                details={
                    "reason": "stage_version_changed",
                    "old_version": old_version,
                    "new_version": stage_version,
                },
            )
            reset.append(page)
        self._session.flush()
        return reset

    def count_by_status(self) -> dict[PageStatus, int]:
        """Number of pages per status (statuses with no pages are omitted)."""
        rows = self._session.execute(
            select(PageModel.status, func.count()).group_by(PageModel.status)
        ).all()
        return {status: count for status, count in rows}

    def count_active(self) -> int:
        """Number of active pages."""
        return self._session.scalar(
            select(func.count()).where(PageModel.status.not_in(INACTIVE_STATUSES))
        ) or 0


class SqlAlchemyReceiptRepository:
    """Stage receipts."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def get(self, url_id: str, stage: str) -> StageReceipt | None:
        """Return the receipt of a page for a stage, or None."""
        model = self._session.get(StageReceiptModel, (url_id, stage))
        return receipt_to_domain(model) if model else None

    def upsert(
        self, url_id: str, stage: str, input_hash: str, stage_version: str, run_id: str
    ) -> StageReceipt:
        """Create or replace a receipt."""
        model = self._session.get(StageReceiptModel, (url_id, stage))
        if model is None:
            model = StageReceiptModel(url_id=url_id, stage=stage)
            self._session.add(model)
        model.input_hash = input_hash
        model.stage_version = stage_version
        model.run_id = run_id
        model.processed_at = self._clock()
        self._session.flush()
        return receipt_to_domain(model)

    def delete_for_page(self, url_id: str, stage: str | None = None) -> int:
        """Delete one or all receipts of a page."""
        stmt = delete(StageReceiptModel).where(StageReceiptModel.url_id == url_id)
        if stage is not None:
            stmt = stmt.where(StageReceiptModel.stage == stage)
        result = self._session.execute(stmt.execution_options(synchronize_session="fetch"))
        return result.rowcount


class SqlAlchemyChunkRepository:
    """Chunks (source of truth for chunk text)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_for_page(self, url_id: str, chunks: Sequence[Chunk]) -> None:
        """Replace all chunks of a page inside a SAVEPOINT: all or nothing."""
        wrong = [c.chunk_id for c in chunks if c.url_id != url_id]
        if wrong:
            raise ValueError(f"Chunks belong to another page than {url_id}: {wrong}")
        with self._session.begin_nested():
            self.delete_for_page(url_id)
            self._session.add_all(chunk_to_model(c) for c in chunks)
            self._session.flush()

    def list_by_page(self, url_id: str) -> list[Chunk]:
        """Chunks of a page ordered by ordinal."""
        models = self._session.scalars(
            select(ChunkModel).where(ChunkModel.url_id == url_id).order_by(ChunkModel.ordinal)
        )
        return [chunk_to_domain(m) for m in models]

    def iter_all(self, batch_size: int = 1000) -> Iterator[Chunk]:
        """All chunks ordered by chunk_id, fetched in keyset-paginated batches."""
        last_id: str | None = None
        while True:
            stmt = select(ChunkModel).order_by(ChunkModel.chunk_id).limit(batch_size)
            if last_id is not None:
                stmt = stmt.where(ChunkModel.chunk_id > last_id)
            batch = self._session.scalars(stmt).all()
            if not batch:
                return
            for model in batch:
                yield chunk_to_domain(model)
            last_id = batch[-1].chunk_id

    def delete_for_page(self, url_id: str) -> int:
        """Delete all chunks of a page."""
        result = self._session.execute(
            delete(ChunkModel)
            .where(ChunkModel.url_id == url_id)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount

    def count(self) -> int:
        """Total number of chunks."""
        return self._session.scalar(select(func.count()).select_from(ChunkModel)) or 0


class SqlAlchemyEventRepository:
    """Per-page event log."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def add(
        self,
        run_id: str,
        url_id: str,
        event: EventType,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> PageEvent:
        """Record an event."""
        model = _add_event(self._session, self._clock, run_id, url_id, event, stage, details)
        return event_to_domain(model)

    def summary(self, run_id: str) -> dict[EventType, int]:
        """Number of events per type in a run."""
        rows = self._session.execute(
            select(PageEventModel.event, func.count())
            .where(PageEventModel.run_id == run_id)
            .group_by(PageEventModel.event)
        ).all()
        return {event: count for event, count in rows}

    def list(self, run_id: str, event: EventType | None = None) -> Sequence[PageEvent]:
        """Events of a run in insertion order, optionally filtered by type."""
        stmt = select(PageEventModel).where(PageEventModel.run_id == run_id)
        if event is not None:
            stmt = stmt.where(PageEventModel.event == event)
        return [event_to_domain(m) for m in self._session.scalars(stmt.order_by(PageEventModel.id))]

