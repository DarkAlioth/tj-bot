"""drop poster columns: cards are typographic, no images are kept

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-11-12 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c9d0e1f2a3b"
down_revision: str | None = "7b8c9d0e1f2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("torrent_meta", "poster_file_id")
    op.drop_column("torrent_meta", "poster_bytes")
    op.drop_column("torrent_meta", "mosaic")


def downgrade() -> None:
    op.add_column("torrent_meta", sa.Column("mosaic", sa.String(length=72)))
    op.add_column("torrent_meta", sa.Column("poster_bytes", sa.LargeBinary()))
    op.add_column("torrent_meta", sa.Column("poster_file_id", sa.String()))
