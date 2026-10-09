"""Explicit, loopback-only development sessions; disabled by default."""

from __future__ import annotations

import time
from typing import Any

import jwt
from fastapi import HTTPException, Request

ISSUER = "pilotree-local-development"
AUDIENCE = "pilotree-local-workspace"


class LocalJWTVerifier:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("LOCAL_DEVELOPMENT_SECRET must contain at least 32 characters")
        self.secret = secret

    def verify(self, token: str) -> dict[str, Any]:
        return jwt.decode(
            token, self.secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE
        )

    def issue(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": "local-developer",
                "email": "developer@localhost",
                "name": "Local Administrator",
                "iss": ISSUER,
                "aud": AUDIENCE,
                "iat": now,
                "exp": now + 28800,
            },
            self.secret,
            algorithm="HS256",
        )


def require_local_request(request: Request) -> None:
    if not request.app.state.settings.local_development_auth:
        raise HTTPException(404, "Local development sign-in is disabled")
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Local sign-in requires a loopback connection")
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:5173", "http://localhost:5173"}:
        raise HTTPException(403, "Local sign-in origin is not allowed")
