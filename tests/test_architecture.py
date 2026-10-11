"""Layering rules that keep the ORM replaceable."""

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from siterag.adapters.sqlalchemy import models
from siterag.domain import entities

SRC = Path(__file__).resolve().parents[1] / "src" / "siterag"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("layer", ["domain", "ports"])
def test_layer_does_not_import_sqlalchemy_or_adapters(layer: str) -> None:
    files = list((SRC / layer).rglob("*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            assert not module.startswith(("sqlalchemy", "alembic", "siterag.adapters")), (
                f"{path.relative_to(SRC)} imports {module}"
            )


@pytest.mark.parametrize(
    ("entity", "model"),
    [
        (entities.Run, models.RunModel),
        (entities.Page, models.PageModel),
        (entities.StageReceipt, models.StageReceiptModel),
        (entities.Chunk, models.ChunkModel),
        (entities.PageEvent, models.PageEventModel),
    ],
)
def test_entity_fields_match_model_columns(entity: type, model: type) -> None:
    entity_fields = {f.name for f in fields(entity)}
    columns = set(model.__table__.columns.keys())  # type: ignore[attr-defined]
    assert entity_fields == columns
