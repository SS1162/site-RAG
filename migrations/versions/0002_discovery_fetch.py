"""discovery and fetch: discovery_depth, skipped/orphan counters

Enum columns are VARCHAR(32) without CHECK constraints (``create_constraint=False``), so
the new values ``PageStatus.skipped``, ``EventType.orphan`` and ``EventType.skipped``
need no DDL; only the new columns are added.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN (no batch mode): SQLite supports it natively, and a batch
    # "move and copy" of ``runs`` would fail because ``pages`` has foreign keys to it.
    op.add_column('pages', sa.Column('discovery_depth', sa.Integer(), nullable=True))
    op.add_column(
        'runs', sa.Column('pages_skipped', sa.Integer(), server_default=sa.text('0'), nullable=False)
    )
    op.add_column(
        'runs', sa.Column('pages_orphaned', sa.Integer(), server_default=sa.text('0'), nullable=False)
    )


def downgrade() -> None:
    # Native DROP COLUMN (SQLite >= 3.35), for the same reason as above.
    op.drop_column('runs', 'pages_orphaned')
    op.drop_column('runs', 'pages_skipped')
    op.drop_column('pages', 'discovery_depth')
