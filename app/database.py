"""SQLAlchemy async engine + session factory + declarative base."""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    """Asegura el driver asyncpg para PostgreSQL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(
    normalize_database_url(settings.database_url),
    pool_pre_ping=True,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: session por request."""
    async with async_session_factory() as session:
        yield session
