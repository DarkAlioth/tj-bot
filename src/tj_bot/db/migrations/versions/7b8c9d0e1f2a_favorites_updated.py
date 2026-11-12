"""favorites updated_at and seen_at

Revision ID: 7b8c9d0e1f2a
Revises: 4f2a9c7b1d3e
Create Date: 2026-11-12 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b8c9d0e1f2a"
down_revision: str | None = "4f2a9c7b1d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # updated_at: the watcher refreshed the snapshot; seen_at: the owner
    # acted on the favorite since — "обновлено" while updated_at > seen_at
    op.add_column(
        "favorites",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "favorites",
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("favorites", "seen_at")
    op.drop_column("favorites", "updated_at")
