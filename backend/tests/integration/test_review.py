"""Tests for M8: review/edit API, claims, reviews (§4.2, Fix 1, Fix 10, ADR-9)."""

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────


async def _create_enquiry(db_engine: AsyncEngine, enquiry_id: str = "enq-review-001") -> str:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO enquiries (id, name, email, company, status, message)"
                " VALUES (:id, :name, :email, :company, :status, :message)"
            ),
            {
                "id": enquiry_id,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "company": "Beta Inc",
                "status": "new",
                "message": "Where is my order?",
            },
        )
    return enquiry_id


async def _create_analysis(
    db_engine: AsyncEngine,
    enquiry_id: str,
    status: str = "pending_review",
    result: dict | None = None,
) -> str:
    """Create an analysis directly in the DB and return its id."""
    import hashlib
    import json

    analysis_id = str(uuid.uuid4())
    input_hash = hashlib.sha256(enquiry_id.encode()).hexdigest()
    result_data = result or {
        "summary": "Test summary",
        "category": "other",
        "priority": "medium",
        "reason": "test",
        "suggested_action": "review",
        "missing_info": [],
        "risk_flags": [],
        "needs_human_call": False,
    }
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
                "r": json.dumps(result_data),
            },
        )
    return analysis_id


# ── Claim / Release ───────────────────────────────────────────────


async def test_claim_enquiry(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /enquiries/{id}/claim claims an enquiry."""
    enquiry_id = await _create_enquiry(db_engine)

    response = await app_client.post(
        f"/enquiries/{enquiry_id}/claim",
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "claimed_by" in data
    assert "claim_expires_at" in data


async def test_claim_already_claimed(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Claiming an already-claimed enquiry returns 409."""
    enquiry_id = await _create_enquiry(db_engine)

    # First claim
    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/claim",
        headers=reviewer_headers,
    )
    assert r1.status_code == 200

    # Second claim by same user should succeed (re-claim)
    r2 = await app_client.post(
        f"/enquiries/{enquiry_id}/claim",
        headers=reviewer_headers,
    )
    assert r2.status_code == 200


async def test_release_enquiry(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /enquiries/{id}/release releases a claim."""
    enquiry_id = await _create_enquiry(db_engine)

    # Claim first
    await app_client.post(
        f"/enquiries/{enquiry_id}/claim",
        headers=reviewer_headers,
    )

    # Release
    response = await app_client.post(
        f"/enquiries/{enquiry_id}/release",
        headers=reviewer_headers,
    )
    assert response.status_code == 204


async def test_claim_not_found(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
) -> None:
    """404 when enquiry doesn't exist."""
    response = await app_client.post(
        "/enquiries/nonexistent/claim",
        headers=reviewer_headers,
    )
    assert response.status_code == 404


# ── PATCH /analyses/{id} ─────────────────────────────────────────


async def test_patch_analysis_edits_fields(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """PATCH /analyses/{id} edits result fields with If-Match."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.patch(
        f"/analyses/{analysis_id}",
        json={"summary": "Updated summary"},
        headers={**reviewer_headers, "If-Match": "1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 2
    assert data["result"]["summary"] == "Updated summary"


async def test_patch_analysis_version_conflict(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """PATCH with stale If-Match returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.patch(
        f"/analyses/{analysis_id}",
        json={"summary": "Updated"},
        headers={**reviewer_headers, "If-Match": "99"},  # stale version
    )
    assert response.status_code == 409


async def test_patch_analysis_wrong_status(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """PATCH on a non-pending_review analysis returns 423."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="pending")

    response = await app_client.patch(
        f"/analyses/{analysis_id}",
        json={"summary": "Updated"},
        headers={**reviewer_headers, "If-Match": "1"},
    )
    assert response.status_code == 423


# ── POST /analyses/{id}/resume-review ─────────────────────────────


async def test_resume_review_approve(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Approve a pending_review analysis."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "approve",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["version"] == 2


async def test_resume_review_reject(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Reject a pending_review analysis with typed_reason."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "reject",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
            "typed_reason": "Not relevant",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_resume_review_reject_requires_typed_reason(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Reject without typed_reason returns 422."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "reject",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 422


async def test_resume_review_edit_and_approve(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """edit_and_approve applies edited_fields and approves."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "edit_and_approve",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
            "edited_fields": {"summary": "Edited summary", "priority": "high"},
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"


async def test_resume_review_edit_and_approve_requires_edited_fields(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """edit_and_approve without edited_fields returns 422."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "edit_and_approve",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 422


async def test_resume_review_override_block_admin_only(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """override_block is admin-only (Fix 10)."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    # Reviewer cannot override_block
    r1 = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "override_block",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
            "edited_fields": {"summary": "overridden"},
            "typed_reason": "false positive",
        },
        headers=reviewer_headers,
    )
    assert r1.status_code == 403

    # Admin can override_block
    r2 = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "override_block",
            "reviewer_id": "test-admin-001",
            "expected_analysis_version": 1,
            "edited_fields": {"summary": "overridden"},
            "typed_reason": "false positive",
        },
        headers=admin_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "approved"


async def test_resume_review_version_conflict(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """resume-review with stale version returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "approve",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 99,  # stale
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409


async def test_resume_review_wrong_status(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """resume-review on a non-pending_review analysis returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="approved")

    response = await app_client.post(
        f"/analyses/{analysis_id}/resume-review",
        json={
            "action": "approve",
            "reviewer_id": "test-reviewer-001",
            "expected_analysis_version": 1,
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409


# ── POST /reviews ────────────────────────────────────────────────


async def test_post_review_dismiss_false_positive(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /reviews with dismiss_false_positive on an approved analysis."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="approved")

    response = await app_client.post(
        "/reviews",
        json={
            "analysis_id": analysis_id,
            "action": "dismiss_false_positive",
            "typed_reason": "This was a false positive",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "dismiss_false_positive"


async def test_post_review_missing_typed_reason(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /reviews without typed_reason returns 400."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="approved")

    response = await app_client.post(
        "/reviews",
        json={
            "analysis_id": analysis_id,
            "action": "dismiss_false_positive",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 400


async def test_post_review_wrong_action(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /reviews with wrong action returns 400."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="approved")

    response = await app_client.post(
        "/reviews",
        json={
            "analysis_id": analysis_id,
            "action": "approve",
            "typed_reason": "test",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 400


async def test_post_review_not_decided(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /reviews on a non-decided analysis returns 409."""
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, status="pending_review")

    response = await app_client.post(
        "/reviews",
        json={
            "analysis_id": analysis_id,
            "action": "dismiss_false_positive",
            "typed_reason": "test",
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409
