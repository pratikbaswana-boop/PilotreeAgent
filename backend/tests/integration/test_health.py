"""Tests for /healthz and /readyz endpoints."""

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_healthz_ok(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "breakers" in body
    assert "listen_connected" in body


async def test_readyz_ok(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True}


async def test_correlation_id_echoed(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/healthz", headers={"X-Request-ID": "test-correlation-123"})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "test-correlation-123"


async def test_correlation_id_generated(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.get("/healthz")
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid is not None
    assert len(rid) > 0
