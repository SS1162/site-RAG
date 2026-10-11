"""Explicit conversion between ORM models and domain entities.

Each entity's dataclass fields map one-to-one to same-named columns; a test checks that
the field and column sets stay in sync. JSON values are deep-copied so that a domain
object never shares a mutable dict/list with an ORM object.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from typing import Any

from siterag.adapters.sqlalchemy.models import (
    ChunkModel,
    PageEventModel,
    PageModel,
    RunModel,
    StageReceiptModel,
)
from siterag.domain.entities import Chunk, Page, PageEvent, Run, StageReceipt

RUN_FIELDS = tuple(f.name for f in fields(Run))
PAGE_FIELDS = tuple(f.name for f in fields(Page))
RECEIPT_FIELDS = tuple(f.name for f in fields(StageReceipt))
CHUNK_FIELDS = tuple(f.name for f in fields(Chunk))
EVENT_FIELDS = tuple(f.name for f in fields(PageEvent))


def _values(source: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: copy.deepcopy(getattr(source, name)) for name in names}


def _assign(target: Any, source: Any, names: tuple[str, ...]) -> None:
    for name, value in _values(source, names).items():
        setattr(target, name, value)


def run_to_domain(model: RunModel) -> Run:
    """ORM run -> domain run."""
    return Run(**_values(model, RUN_FIELDS))


def page_to_domain(model: PageModel) -> Page:
    """ORM page -> domain page."""
    return Page(**_values(model, PAGE_FIELDS))


def page_to_model(page: Page, model: PageModel | None = None) -> PageModel:
    """Copy a domain page onto an ORM page (a new one if ``model`` is None)."""
    model = model if model is not None else PageModel()
    _assign(model, page, PAGE_FIELDS)
    return model


def receipt_to_domain(model: StageReceiptModel) -> StageReceipt:
    """ORM receipt -> domain receipt."""
    return StageReceipt(**_values(model, RECEIPT_FIELDS))


def chunk_to_domain(model: ChunkModel) -> Chunk:
    """ORM chunk -> domain chunk."""
    return Chunk(**_values(model, CHUNK_FIELDS))


def chunk_to_model(chunk: Chunk) -> ChunkModel:
    """Domain chunk -> new ORM chunk."""
    model = ChunkModel()
    _assign(model, chunk, CHUNK_FIELDS)
    return model


def event_to_domain(model: PageEventModel) -> PageEvent:
    """ORM event -> domain event."""
    return PageEvent(**_values(model, EVENT_FIELDS))
