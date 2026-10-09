"""Explicit local development authentication still uses verified bearer tokens."""

import pytest

from app.main import app
from app.security.local_auth import LocalJWTVerifier

pytestmark = pytest.mark.asyncio


async def test_local_login_disabled_by_default(app_client):
    response = await app_client.post("/auth/local-session")
    assert response.status_code == 404


async def test_local_login_verifies_session_and_rejects_other_origins(app_client):
    original_verifier = app.state.jwt_verifier
    app.state.settings.local_development_auth = True
    app.state.jwt_verifier = LocalJWTVerifier("test-only-local-secret-with-at-least-32-characters")
    try:
        rejected = await app_client.post(
            "/auth/local-session", headers={"Origin": "https://untrusted.example"}
        )
        assert rejected.status_code == 403
        response = await app_client.post(
            "/auth/local-session", headers={"Origin": "http://127.0.0.1:5173"}
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        me = await app_client.get("/me", headers={"Authorization": "Bearer " + token})
        assert me.status_code == 200
        assert me.json()["role"] == "reviewer"
        invalid = await app_client.get("/me", headers={"Authorization": "Bearer invalid-token"})
        assert invalid.status_code == 401
    finally:
        app.state.settings.local_development_auth = False
        app.state.jwt_verifier = original_verifier
