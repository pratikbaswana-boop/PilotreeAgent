"""Auth router — GET /me (viewer+).

Returns the authenticated user's identity and role for the frontend auth
guard (DECISIONS.md #3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db_session
from app.domain.enums import UserRole
from app.domain.models import User
from app.security.auth import get_current_user

router = APIRouter(tags=["auth"])


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
    }


@router.post("/auth/local-session")
async def local_session(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    from app.security.local_auth import LocalJWTVerifier, require_local_request

    require_local_request(request)
    user = await db.scalar(select(User).where(User.oidc_subject == "local-developer"))
    if user is None:
        user = User(
            oidc_subject="local-developer",
            email="developer@localhost",
            display_name="Local Reviewer",
            role=UserRole.reviewer,
        )
        db.add(user)
        await db.flush()
    verifier = request.app.state.jwt_verifier
    if not isinstance(verifier, LocalJWTVerifier):
        raise HTTPException(503, "Local session verifier is unavailable")
    return {"access_token": verifier.issue(), "token_type": "Bearer"}
