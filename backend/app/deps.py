"""FastAPI dependencies (§3.1 deps.py).

M1 provides the DB session dependency. ETag/current_user deps are wired
through security/auth.py and added here as milestones need them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import DBManager  # noqa: F401 — re-exported for convenience


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped async session from the transaction-mode pool."""
    db: DBManager = request.app.state.db
    async with db.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
