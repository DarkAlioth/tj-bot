"""torrent meta cache

Revision ID: 6a7b8c9d0e1f
Revises: 4f2a9c7b1d3e
Create Date: 2026-11-12 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6a7b8c9d0e1f"
down_revision: str | None = "4f2a9c7b1d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "torrent_meta",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("tracker", sa.String(length=128), nullable=False),
        sa.Column("details_url", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("poster_file_id", sa.String(), nullable=True),
        sa.Column("poster_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("mosaic", sa.String(length=72), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("hash"),
    )
    op.create_index("ix_torrent_meta_fetched_at", "torrent_meta", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_torrent_meta_fetched_at", table_name="torrent_meta")
    op.drop_table("torrent_meta")
