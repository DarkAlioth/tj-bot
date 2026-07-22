"""drop subscriptions

Revision ID: e1a726f5e1a9
Revises: 34ec4ac3853b
Create Date: 2026-07-22 20:33:05.369904

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1a726f5e1a9"
down_revision: str | None = "34ec4ac3853b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("subscription_seen")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_table("subscriptions")


def downgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("chat_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "query_text", sa.VARCHAR(length=256), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("subscriptions_pkey")),
    )
    op.create_index(
        op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False
    )
    op.create_table(
        "subscription_seen",
        sa.Column("subscription_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "torrent_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("subscription_seen_subscription_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "subscription_id", "torrent_hash", name=op.f("subscription_seen_pkey")
        ),
    )
