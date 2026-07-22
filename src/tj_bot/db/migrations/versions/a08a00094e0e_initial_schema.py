"""initial schema

Revision ID: a08a00094e0e
Revises:
Create Date: 2026-07-22 15:25:42.366636

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a08a00094e0e"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy pre-Alembic cache tables are disposable and structurally
    # incompatible — drop them before creating the managed schema.
    op.execute("DROP TABLE IF EXISTS torrents CASCADE")
    op.execute('DROP TABLE IF EXISTS "query" CASCADE')

    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_search_queries_hash"), "search_queries", ["hash"], unique=True
    )
    op.create_table(
        "torrents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("uploader", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("details_url", sa.String(), nullable=False),
        sa.Column("download_url", sa.String(), nullable=False),
        sa.Column("seeders", sa.Integer(), nullable=False),
        sa.Column("peers", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.Date(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_torrents_category"), "torrents", ["category"], unique=False
    )
    op.create_index(op.f("ix_torrents_hash"), "torrents", ["hash"], unique=True)
    op.create_table(
        "search_query_torrents",
        sa.Column("query_id", sa.Integer(), nullable=False),
        sa.Column("torrent_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_id"], ["search_queries.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["torrent_id"], ["torrents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("query_id", "torrent_id"),
    )


def downgrade() -> None:
    op.drop_table("search_query_torrents")
    op.drop_index(op.f("ix_torrents_hash"), table_name="torrents")
    op.drop_index(op.f("ix_torrents_category"), table_name="torrents")
    op.drop_table("torrents")
    op.drop_index(op.f("ix_search_queries_hash"), table_name="search_queries")
    op.drop_table("search_queries")
