"""favorites

Revision ID: 091888754795
Revises: 17975222ed18
Create Date: 2026-07-23 00:15:22.575211

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "091888754795"
down_revision: str | None = "17975222ed18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("torrent_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("tracker", sa.String(length=128), nullable=False),
        sa.Column("details_url", sa.String(), nullable=False),
        sa.Column("download_url", sa.String(), nullable=False),
        sa.Column("seeders", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "torrent_hash"),
    )
    op.create_index(
        op.f("ix_favorites_user_id"), "favorites", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_favorites_user_id"), table_name="favorites")
    op.drop_table("favorites")
