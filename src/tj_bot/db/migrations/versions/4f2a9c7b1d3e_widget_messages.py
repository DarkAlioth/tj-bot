"""widget messages and classic ui flag

Revision ID: 4f2a9c7b1d3e
Revises: b7d1a90c3f21
Create Date: 2026-11-12 18:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4f2a9c7b1d3e"
down_revision: str | None = "b7d1a90c3f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bot_users",
        sa.Column(
            "classic_ui", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_table(
        "widget_messages",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("chat_id", "message_id"),
    )


def downgrade() -> None:
    op.drop_table("widget_messages")
    op.drop_column("bot_users", "classic_ui")
