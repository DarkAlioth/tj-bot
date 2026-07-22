"""search history and tracker

Revision ID: 35f4b4a4d2b3
Revises: a08a00094e0e
Create Date: 2026-07-22 16:37:35.670492

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "35f4b4a4d2b3"
down_revision: str | None = "a08a00094e0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("query_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["search_queries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_search_events_query_id"), "search_events", ["query_id"], unique=False
    )
    op.create_index(
        op.f("ix_search_events_user_id"), "search_events", ["user_id"], unique=False
    )
    op.add_column(
        "search_queries", sa.Column("query_text", sa.String(length=256), nullable=True)
    )
    op.create_index(
        op.f("ix_search_queries_query_text"),
        "search_queries",
        ["query_text"],
        unique=False,
    )
    op.add_column(
        "torrents",
        sa.Column("tracker", sa.String(length=128), server_default="", nullable=False),
    )
    op.create_index(op.f("ix_torrents_tracker"), "torrents", ["tracker"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_torrents_tracker"), table_name="torrents")
    op.drop_column("torrents", "tracker")
    op.drop_index(op.f("ix_search_queries_query_text"), table_name="search_queries")
    op.drop_column("search_queries", "query_text")
    op.drop_index(op.f("ix_search_events_user_id"), table_name="search_events")
    op.drop_index(op.f("ix_search_events_query_id"), table_name="search_events")
    op.drop_table("search_events")
