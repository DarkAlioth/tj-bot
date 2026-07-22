"""subscriptions

Revision ID: 34ec4ac3853b
Revises: 35f4b4a4d2b3
Create Date: 2026-07-22 16:49:22.847516

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "34ec4ac3853b"
down_revision: str | None = "35f4b4a4d2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("query_text", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False
    )
    op.create_table(
        "subscription_seen",
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("torrent_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("subscription_id", "torrent_hash"),
    )


def downgrade() -> None:
    op.drop_table("subscription_seen")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
