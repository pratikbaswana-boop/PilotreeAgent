"""JWT/OIDC verification and RBAC dependencies (§3.1 security/auth.py).

Production uses `JWKSJWTVerifier` (PyJWKClient fetches the OIDC provider's
public keys).  Tests substitute a `StaticKeyJWTVerifier` (defined in tests/)
that verifies against a generated RSA key pair — no external dependency.

Role hierarchy: viewer < reviewer < admin (see `UserRole.rank`).
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db_session
from app.domain.enums import UserRole
from app.domain.models import User
from app.observability.logging import get_logger

logger = get_logger()


class JWTVerifier(Protocol):
    """Verify a raw JWT string and return the decoded claims dict."""

    def verify(self, token: str) -> dict[str, Any]: ...


class JWKSJWTVerifier:
    """Production verifier: fetches signing keys from the OIDC JWKS URI."""

    def __init__(self, jwks_uri: str, issuer: str, client_id: str, leeway: int) -> None:
        self._jwks_client = PyJWKClient(jwks_uri)
        self._issuer = issuer
        self._client_id = client_id
        self._leeway = leeway

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(  # type: ignore[no-any-return]
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=self._issuer,
            audience=self._client_id,
            leeway=self._leeway,
        )


def _extract_bearer(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
        )
    return auth_header[len("Bearer ") :]


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Verify the JWT, JIT-provision the user row, return the User.

    On first authenticated request the user is created with the default
    `viewer` role (DECISIONS.md #1).
    """
    token = _extract_bearer(request)
    verifier: JWTVerifier = request.app.state.jwt_verifier
    try:
        claims = verifier.verify(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired — please sign in again.",
        ) from None
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        ) from None

    oidc_subject = claims.get("sub")
    if not oidc_subject or not isinstance(oidc_subject, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim.",
        )

    email = str(claims.get("email", ""))
    display_name = str(claims.get("name", claims.get("preferred_username", email)))

    result = await db.execute(select(User).where(User.oidc_subject == oidc_subject))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=uuid.uuid4(),
            oidc_subject=oidc_subject,
            email=email,
            display_name=display_name,
            role=UserRole.viewer,
        )
        db.add(user)
        await db.flush()
        logger.info("user.jit_provisioned", user_id=str(user.id))
    elif user.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been disabled.",
        )

    return user


def require_role(min_role: UserRole) -> Any:
    """FastAPI dependency factory: enforce minimum role via `UserRole.rank`."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role.rank < min_role.rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to do that.",
            )
        return user

    return _check
