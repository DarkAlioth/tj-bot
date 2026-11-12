import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Torrent(Base):
    __tablename__ = "torrents"

    id: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str]
    uploader: Mapped[str | None]
    description: Mapped[str | None]
    category: Mapped[str] = mapped_column(index=True)
    tracker: Mapped[str] = mapped_column(String(128), server_default="", index=True)
    details_url: Mapped[str]
    download_url: Mapped[str]
    seeders: Mapped[int]
    peers: Mapped[int]
    published_at: Mapped[datetime.date]
    size: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    query_text: Mapped[str | None] = mapped_column(String(256), index=True)
    result_count: Mapped[int]
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SearchQueryTorrent(Base):
    __tablename__ = "search_query_torrents"

    query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), primary_key=True
    )
    torrent_id: Mapped[int] = mapped_column(
        ForeignKey("torrents.id", ondelete="CASCADE"), primary_key=True
    )


class SearchEvent(Base):
    __tablename__ = "search_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BotUser(Base):
    __tablename__ = "bot_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(256))
    blocked: Mapped[bool] = mapped_column(server_default=text("false"))
    admin: Mapped[bool] = mapped_column(server_default=text("false"))
    # opted out of widgets (old client): text cards and inline keyboards
    classic_ui: Mapped[bool] = mapped_column(server_default=text("false"))
    first_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Favorite(Base):
    """A user's starred torrent — denormalized snapshot immune to cache TTL."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "torrent_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    torrent_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str]
    category: Mapped[str]
    tracker: Mapped[str] = mapped_column(String(128))
    details_url: Mapped[str]
    download_url: Mapped[str]
    seeders: Mapped[int]
    size: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[datetime.date]
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DownloadEvent(Base):
    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WidgetMessage(Base):
    """Context of a sent widget: a widget_query carries no state of its own."""

    __tablename__ = "widget_messages"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rev: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TorrentMetaRow(Base):
    """Scraped tracker-page data per torrent hash; outlives the result cache."""

    __tablename__ = "torrent_meta"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tracker: Mapped[str] = mapped_column(String(128))
    details_url: Mapped[str]
    # ok | error | not_found | challenge | login | unsupported
    status: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Telegram file_id after the first sendPhoto; the prepared JPEG until then
    poster_file_id: Mapped[str | None]
    poster_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    # 4×6 ambient mosaic, 12-bit rgb per cell (72 hex), None for generated covers
    mosaic: Mapped[str | None] = mapped_column(String(72))
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(200))
