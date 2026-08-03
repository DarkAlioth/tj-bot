"""drop magnet links

Revision ID: b7d1a90c3f21
Revises: 091888754795
Create Date: 2026-08-03 21:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1a90c3f21"
down_revision: str | None = "091888754795"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # the file picker keeps magnet state in qBittorrent itself now
    op.drop_table("magnet_links")


def downgrade() -> None:
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
