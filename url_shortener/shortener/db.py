import os
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.environ.get("SHORTENER_DATABASE_URL", "postgresql+asyncpg://localhost:55432/url_shortener")


class Base(DeclarativeBase):
    pass


class Link(Base):
    __tablename__ = "links"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    target_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Click(Base):
    __tablename__ = "clicks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("links.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    referrer: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_clicks_link_at", "link_id", "at"),)


def make_engine(url: str = DATABASE_URL) -> AsyncEngine:
    return create_async_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
