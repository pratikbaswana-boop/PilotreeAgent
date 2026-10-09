"""Async database engines and session factories.

Two pools per ADR-16 / Fix 9:
  - **transaction-mode** (`database_url`): ordinary request-scoped queries.
  - **session-mode** (`database_session_pool_url`): LISTEN, advisory-lock
    holders, LangGraph checkpointer (used from M4/M7 onward).

M1 uses the transaction-mode pool for request handlers and health checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


@dataclass
class DBManager:
    """Holds both engines + session factory; disposed on shutdown."""

    engine: AsyncEngine
    session_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()
        await self.session_engine.dispose()


def create_db(settings: Settings) -> DBManager:
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
    )
    session_engine = create_async_engine(
        settings.database_session_pool_url,
        pool_size=settings.database_session_pool_size,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return DBManager(
        engine=engine,
        session_engine=session_engine,
        session_factory=session_factory,
    )
