"""Tests for JWT/OIDC auth, RBAC deps, and GET /me."""

import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.enums import UserRole
from app.domain.models import User
from app.security.auth import require_role

pytestmark = pytest.mark.asyncio


# ── GET /me ───────────────────────────────────────────────────────


async def test_me_no_token(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/me")
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"] or "Authorization" in resp.json()["detail"]


async def test_me_valid_token(
    app_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await app_client.get("/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "reviewer@pilotree.example"
    assert body["role"] == "viewer"
    assert uuid.UUID(body["id"])  # is a valid UUID


async def test_me_jit_creates_user(
    app_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    resp = await app_client.get("/me", headers=auth_headers)
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT oidc_subject, email, role FROM users WHERE id = :id"),
            {"id": uuid.UUID(user_id)},
        )
        row = result.fetchone()
    assert row is not None
    assert row.oidc_subject == "test-subject-001"
    assert row.email == "reviewer@pilotree.example"
    assert row.role == "viewer"


async def test_me_expired_token(
    app_client: httpx.AsyncClient,
    expired_token: str,
) -> None:
    resp = await app_client.get("/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


async def test_me_invalid_token(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert "Invalid" in resp.json()["detail"]


async def test_me_missing_sub_claim(
    app_client: httpx.AsyncClient,
    rsa_key_pair: tuple[bytes, bytes],
) -> None:
    """A JWT without 'sub' should be rejected with 401."""
    import time

    import jwt as pyjwt

    private_pem, _ = rsa_key_pair
    now = int(time.time())
    payload = {
        "email": "no-sub@example.com",
        "iss": "https://test.example.com",
        "aud": "triage-test",
        "iat": now,
        "exp": now + 3600,
    }
    token = pyjwt.encode(payload, private_pem, algorithm="RS256")
    resp = await app_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# ── require_role unit tests ────────────────────────────────────────


async def test_require_role_viewer_denied_admin() -> None:
    user = User(
        id=uuid.uuid4(),
        oidc_subject="sub",
        email="v@example.com",
        display_name="Viewer",
        role=UserRole.viewer,
    )
    check = require_role(UserRole.admin)
    with pytest.raises(HTTPException) as exc:
        await check(user=user)
    assert exc.value.status_code == 403


async def test_require_role_admin_allowed() -> None:
    user = User(
        id=uuid.uuid4(),
        oidc_subject="sub",
        email="a@example.com",
        display_name="Admin",
        role=UserRole.admin,
    )
    check = require_role(UserRole.reviewer)
    result = await check(user=user)
    assert result.id == user.id


async def test_require_role_reviewer_denied_admin() -> None:
    user = User(
        id=uuid.uuid4(),
        oidc_subject="sub",
        email="r@example.com",
        display_name="Reviewer",
        role=UserRole.reviewer,
    )
    check = require_role(UserRole.admin)
    with pytest.raises(HTTPException) as exc:
        await check(user=user)
    assert exc.value.status_code == 403


async def test_require_role_viewer_allowed_viewer() -> None:
    user = User(
        id=uuid.uuid4(),
        oidc_subject="sub",
        email="v@example.com",
        display_name="Viewer",
        role=UserRole.viewer,
    )
    check = require_role(UserRole.viewer)
    result = await check(user=user)
    assert result.id == user.id
