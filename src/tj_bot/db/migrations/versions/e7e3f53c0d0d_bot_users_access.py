"""bot users access

Revision ID: e7e3f53c0d0d
Revises: e1a726f5e1a9
Create Date: 2026-07-22 22:04:17.993695

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7e3f53c0d0d"
down_revision: str | None = "e1a726f5e1a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_users",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column(
            "blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_users")
