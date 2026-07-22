"""download events

Revision ID: dc00de579fe9
Revises: e7e3f53c0d0d
Create Date: 2026-07-22 22:12:06.845511

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dc00de579fe9"
down_revision: str | None = "e7e3f53c0d0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_download_events_user_id"), "download_events", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_download_events_user_id"), table_name="download_events")
    op.drop_table("download_events")
