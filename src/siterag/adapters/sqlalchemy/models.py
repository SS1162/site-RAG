"""ORM models. Separate from domain entities; see mappers.py for the conversion."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Enum, ForeignKey, Integer, MetaData, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from siterag.adapters.sqlalchemy.types import UTCDateTime
from siterag.domain.enums import ContentType, DiscoveredVia, EventType, PageStatus, RunStatus

# Named constraints are required for Alembic batch migrations on SQLite.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _enum(enum_cls: type[StrEnum]) -> Enum:
    """Store an enum as its string value (VARCHAR, no CHECK constraint)."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def _json_default() -> Any:
    """JSON column defaulting to ``{}`` both in Python and in the database."""
    return mapped_column(JSON, default=dict, server_default=text("'{}'"))


def _counter() -> Any:
    """INTEGER column defaulting to 0 both in Python and in the database."""
    return mapped_column(Integer, default=0, server_default=text("0"))


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RunModel(Base):
    """Row of ``runs``."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    site_id: Mapped[str] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    command: Mapped[str] = mapped_column(Text)
    args: Mapped[dict[str, Any]] = _json_default()
    config_hash: Mapped[str] = mapped_column(Text)
    site_profile_hash: Mapped[str] = mapped_column(Text)
    pages_new: Mapped[int] = _counter()
    pages_changed: Mapped[int] = _counter()
    pages_unchanged: Mapped[int] = _counter()
    pages_removed: Mapped[int] = _counter()
    pages_failed: Mapped[int] = _counter()
    pages_redirected: Mapped[int] = _counter()
    abort_reason: Mapped[str | None] = mapped_column(Text)


class PageModel(Base):
    """Row of ``pages``."""

    __tablename__ = "pages"

    url_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    url_display: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("pages.url_id"), index=True)
    redirected_to: Mapped[str | None] = mapped_column(ForeignKey("pages.url_id"))
    status: Mapped[PageStatus] = mapped_column(_enum(PageStatus), index=True)
    content_type: Mapped[ContentType | None] = mapped_column(_enum(ContentType))
    discovered_via: Mapped[DiscoveredVia] = mapped_column(_enum(DiscoveredVia))
    found_on: Mapped[str | None] = mapped_column(ForeignKey("pages.url_id"))
    sitemap_lastmod: Mapped[datetime | None] = mapped_column(UTCDateTime)
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    raw_hash: Mapped[str | None] = mapped_column(Text)
    clean_hash: Mapped[str | None] = mapped_column(Text)
    chunks_hash: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    page_type: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = _json_default()
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    last_fetch_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_fetch_ok_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id"))
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fail_count: Mapped[int] = _counter()
    failed_stage: Mapped[str | None] = mapped_column(Text)
    failed_stage_version: Mapped[str | None] = mapped_column(Text)
    last_failed_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id"))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class StageReceiptModel(Base):
    """Row of ``stage_receipts``."""

    __tablename__ = "stage_receipts"

    url_id: Mapped[str] = mapped_column(ForeignKey("pages.url_id"), primary_key=True)
    stage: Mapped[str] = mapped_column(Text, primary_key=True)
    input_hash: Mapped[str] = mapped_column(Text)
    stage_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ChunkModel(Base):
    """Row of ``chunks``."""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    url_id: Mapped[str] = mapped_column(ForeignKey("pages.url_id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = _json_default()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PageEventModel(Base):
    """Row of ``page_events``."""

    __tablename__ = "page_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    url_id: Mapped[str] = mapped_column(ForeignKey("pages.url_id"), index=True)
    event: Mapped[EventType] = mapped_column(_enum(EventType))
    stage: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = _json_default()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
