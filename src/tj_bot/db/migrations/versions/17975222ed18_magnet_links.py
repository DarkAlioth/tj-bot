"""magnet links

Revision ID: 17975222ed18
Revises: dc00de579fe9
Create Date: 2026-07-22 23:30:24.607337

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "17975222ed18"
down_revision: str | None = "dc00de579fe9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "magnet_links",
        sa.Column("hash", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("hash"),
    )


def downgrade() -> None:
    op.drop_table("magnet_links")
