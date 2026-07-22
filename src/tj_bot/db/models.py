import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func, text
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
    first_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MagnetLink(Base):
    """Pending magnet awaiting category choice; survives bot restarts."""

    __tablename__ = "magnet_links"

    hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str]
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
