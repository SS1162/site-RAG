"""Stage port: one step of the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from siterag.pipeline.context import RunContext


@dataclass
class StageResult:
    """What a stage reports back to the runner (shown in logs)."""

    name: str
    summary: dict[str, Any] = field(default_factory=dict)


class Stage(Protocol):
    """A pipeline stage. Dependencies come through the constructor; state through ``ctx``.

    A stage stops the run by raising ``RunAbortedError`` (or ``DiscoveryTooSmallError``).
    """

    name: str

    def run(self, ctx: RunContext) -> StageResult:
        """Do the stage's work for the run described by ``ctx``."""
        ...
