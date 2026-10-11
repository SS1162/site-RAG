"""Run Alembic programmatically against a site's state.db."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def alembic_config(db_path: Path) -> Config:
    """Build an Alembic config for ``db_path`` without relying on the working directory."""
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"Migrations directory not found: {MIGRATIONS_DIR}")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["db_path"] = db_path
    return config


def upgrade(db_path: Path, revision: str = "head") -> None:
    """Apply migrations up to ``revision``, creating the database directory if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(db_path), revision)


def downgrade(db_path: Path, revision: str) -> None:
    """Revert migrations down to ``revision``."""
    command.downgrade(alembic_config(db_path), revision)


def check(db_path: Path) -> None:
    """Raise if the database schema differs from the ORM models (``alembic check``)."""
    command.check(alembic_config(db_path))
