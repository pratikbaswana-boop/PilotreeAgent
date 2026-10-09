"""Tests for M9: actions, tools, retry, and outbox endpoints (§4.2, Fix 2, Fix 3, Fix 8)."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────


async def _create_enquiry(db_engine: AsyncEngine, enquiry_id: str = "enq-act-001") -> str:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO enquiries (id, name, email, company, status, message)"
                " VALUES (:id, :name, :email, :company, :status, :message)"
            ),
            {
                "id": enquiry_id,
                "name": "Test User",
                "email": "test@example.com",
                "company": "Test Co",
                "status": "new",
                "message": "Test message",
            },
        )
    return enquiry_id


async def _create_analysis(
    db_engine: AsyncEngine,
    enquiry_id: str,
    status: str = "approved",
) -> str:
    import hashlib

    analysis_id = str(uuid.uuid4())
    input_hash = hashlib.sha256(enquiry_id.encode()).hexdigest()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO analyses"
                " (id, enquiry_id, analysis_attempt, input_hash,"
                " prompt_version, schema_version, status, result)"
                " VALUES (:id, :eid, 1, :ih, 'v1', 'v1', :st, CAST(:r AS JSONB))"
            ),
            {
                "id": analysis_id,
                "eid": enquiry_id,
                "ih": input_hash,
                "st": status,
                "r": json.dumps({
                    "summary": "Approved analysis",
                    "category": "delivery_issue",
                    "priority": "high",
                    "reason": "Late delivery",
                    "suggested_action": "Contact carrier",
                    "missing_info": [],
                    "risk_flags": [],
                    "needs_human_call": False,
                }),
            },
        )
    return analysis_id


async def _seed_tool_config(db_engine: AsyncEngine, key: str, enabled: bool = True) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tool_configs (key, enabled, config)"
                " VALUES (:key, :enabled, '{}'::jsonb)"
                " ON CONFLICT (key) DO UPDATE SET enabled = :enabled"
            ),
            {"key": key, "enabled": enabled},
        )


async def _create_action(
    db_engine: AsyncEngine,
    enquiry_id: str,
    analysis_id: str,
    destination: str = "slack",
    status: str = "failed",
) -> str:
    action_id = str(uuid.uuid4())
    idem_key = f"{enquiry_id}:{destination}"
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO actions"
                " (id, enquiry_id, analysis_id, analysis_version, destination,"
                " idempotency_key, payload, status, requested_by)"
                " VALUES (:id, :eid, :aid, 1, :dest, :ik, '{}'::jsonb, :st,"
                " (SELECT id FROM users WHERE oidc_subject = 'test-reviewer-001' LIMIT 1))"
            ),
            {
                "id": action_id,
                "eid": enquiry_id,
                "aid": analysis_id,
                "dest": destination,
                "ik": idem_key,
                "st": status,
            },
        )
    return action_id


# ── POST /actions ─────────────────────────────────────────────────


async def test_create_actions_success(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions creates action rows for selected destinations."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack", enabled=True)
    await _seed_tool_config(db_engine, "linear", enabled=True)

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack", "linear"],
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 202
    data = response.json()
    assert len(data["actions"]) == 2
    destinations = {a["destination"] for a in data["actions"]}
    assert destinations == {"slack", "linear"}


async def test_create_actions_not_approved(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions fails when analysis is not approved."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="pending_review")

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack"],
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 423


async def test_create_actions_version_conflict(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions with stale version returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack"],
            "expected_analysis_version": 99,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409


async def test_create_actions_disabled_destination(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions to a disabled destination returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack", enabled=False)

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack"],
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409


async def test_create_actions_already_sent(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions to an already-sent destination returns 409 (Fix 3)."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack", enabled=True)
    await _create_action(db_engine, enquiry_id, analysis_id, "slack", "sent")

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack"],
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409


async def test_create_actions_resend(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions with resend=true allows re-sending to same destination."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack", enabled=True)
    await _create_action(db_engine, enquiry_id, analysis_id, "slack", "sent")

    response = await app_client.post(
        "/actions",
        json={
            "analysis_id": analysis_id,
            "destinations": ["slack"],
            "expected_analysis_version": 1,
            "resend": True,
            "resend_reason": "Retrying due to channel issue",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 202


# ── GET /actions ──────────────────────────────────────────────────


async def test_get_actions(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """GET /actions?enquiry_id= returns actions for the enquiry."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _create_action(db_engine, enquiry_id, analysis_id, "slack", "sent")

    response = await app_client.get(
        f"/actions?enquiry_id={enquiry_id}",
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["destination"] == "slack"
    assert data[0]["status"] == "sent"


# ── POST /actions/{id}/retry ──────────────────────────────────────


async def test_retry_failed_action(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /actions/{id}/retry retries a failed action."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    action_id = await _create_action(db_engine, enquiry_id, analysis_id, "slack", "failed")

    response = await app_client.post(
        f"/actions/{action_id}/retry",
        json={"confirm": True, "typed_reason": "Retrying"},
        headers=reviewer_headers,
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pending"


async def test_retry_unknown_action_requires_confirm(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Retrying an unknown action requires confirm=true and typed_reason (Fix 8)."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    action_id = await _create_action(db_engine, enquiry_id, analysis_id, "slack", "unknown")

    # Without confirm — should fail
    r1 = await app_client.post(
        f"/actions/{action_id}/retry",
        json={},
        headers=reviewer_headers,
    )
    assert r1.status_code == 422

    # With confirm — should succeed
    r2 = await app_client.post(
        f"/actions/{action_id}/retry",
        json={"confirm": True, "typed_reason": "May have sent"},
        headers=reviewer_headers,
    )
    assert r2.status_code == 202


async def test_retry_wrong_status(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Retry on a sent action returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    action_id = await _create_action(db_engine, enquiry_id, analysis_id, "slack", "sent")

    response = await app_client.post(
        f"/actions/{action_id}/retry",
        json={"confirm": True, "typed_reason": "Retrying"},
        headers=reviewer_headers,
    )
    assert response.status_code == 409


# ── GET /tools/metadata ───────────────────────────────────────────


async def test_get_tools_metadata(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """GET /tools/metadata returns tool UI metadata."""
    await _seed_tool_config(db_engine, "slack", enabled=True)
    await _seed_tool_config(db_engine, "linear", enabled=False)

    response = await app_client.get("/tools/metadata", headers=reviewer_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    slack = next((t for t in data if t["key"] == "slack"), None)
    linear = next((t for t in data if t["key"] == "linear"), None)
    assert slack is not None and slack["enabled"] is True
    assert linear is not None and linear["enabled"] is False


# ── Admin tool config ─────────────────────────────────────────────


async def test_admin_get_tools(
    app_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """GET /admin/tools returns all tool configs."""
    await _seed_tool_config(db_engine, "slack", enabled=True)
    await _seed_tool_config(db_engine, "linear", enabled=True)

    response = await app_client.get("/admin/tools", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


async def test_admin_update_tool(
    app_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """PUT /admin/tools/{key} updates a tool config."""
    await _seed_tool_config(db_engine, "slack", enabled=True)

    response = await app_client.put(
        "/admin/tools/slack",
        json={"enabled": False},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False


async def test_admin_update_tool_reviewer_forbidden(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """PUT /admin/tools/{key} requires admin role."""
    await _seed_tool_config(db_engine, "slack", enabled=True)

    response = await app_client.put(
        "/admin/tools/slack",
        json={"enabled": False},
        headers=reviewer_headers,
    )
    assert response.status_code == 403
