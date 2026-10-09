"""Tests for M7: analysis endpoints, single-flight, and SSE (§4.2, Fix 5, Fix 7)."""

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────


async def _create_enquiry(db_engine: AsyncEngine, enquiry_id: str = "enq-001") -> str:
    """Create a test enquiry and return its id."""
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO enquiries (id, name, email, company, status, message)"
                " VALUES (:id, :name, :email, :company, :status, :message)"
            ),
            {
                "id": enquiry_id,
                "name": "John Doe",
                "email": "john@example.com",
                "company": "Acme Corp",
                "status": "new",
                "message": "My delivery is late, order #12345.",
            },
        )
    return enquiry_id


# ── POST /enquiries/{id}/analyses ────────────────────────────────


async def test_start_analysis_creates_job_and_analysis(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """POST /enquiries/{id}/analyses creates a new analysis + job."""
    enquiry_id = await _create_enquiry(db_engine)

    response = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["attached"] is False
    assert data["reused"] is False
    assert data["job_id"] is not None
    assert data["analysis_id"] is not None

    # Verify analysis was created in DB
    async with AsyncSession(db_engine) as session:
        result = await session.execute(
            text("SELECT status, analysis_attempt FROM analyses WHERE id = :id"),
            {"id": data["analysis_id"]},
        )
        row = result.fetchone()
        assert row is not None
        assert row.status == "pending"
        assert row.analysis_attempt == 1


async def test_start_analysis_single_flight_reuses_in_flight(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Single-flight: second call with same input returns existing in-flight analysis."""
    enquiry_id = await _create_enquiry(db_engine)

    # First call creates the analysis
    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    assert r1.status_code == 202
    first = r1.json()

    # Second call should return the same in-flight analysis (single-flight)
    r2 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    assert r2.status_code == 202
    second = r2.json()

    assert second["analysis_id"] == first["analysis_id"]
    assert second["attached"] is True
    assert second["reused"] is False


async def test_start_analysis_reuses_completed(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Reuses a completed analysis with the same input_hash (Fix 5)."""
    enquiry_id = await _create_enquiry(db_engine)

    # First call creates the analysis
    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    first = r1.json()

    # Mark the analysis as completed (pending_review)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE analyses SET status = 'pending_review'"
                " WHERE id = :id"
            ),
            {"id": first["analysis_id"]},
        )

    # Second call should reuse the completed analysis
    r2 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    assert r2.status_code == 202
    second = r2.json()

    assert second["analysis_id"] == first["analysis_id"]
    assert second["reused"] is True
    assert second["job_id"] is None


async def test_start_analysis_force_creates_new_attempt(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """force=true creates a new attempt even if a completed analysis exists."""
    enquiry_id = await _create_enquiry(db_engine)

    # First call
    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    first = r1.json()

    # Mark as completed
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE analyses SET status = 'pending_review' WHERE id = :id"),
            {"id": first["analysis_id"]},
        )

    # Force a new analysis
    r2 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": True},
        headers=reviewer_headers,
    )
    assert r2.status_code == 202
    second = r2.json()

    assert second["analysis_id"] != first["analysis_id"]
    assert second["attached"] is False
    assert second["reused"] is False


async def test_start_analysis_not_found(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
) -> None:
    """404 when enquiry doesn't exist."""
    response = await app_client.post(
        "/enquiries/nonexistent/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    assert response.status_code == 404


async def test_start_analysis_requires_reviewer(
    app_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Viewer role (default JIT) cannot start analysis — needs reviewer."""
    enquiry_id = await _create_enquiry(db_engine)

    response = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=auth_headers,  # viewer token
    )
    # The JIT-provisioned user is a viewer, so 403
    assert response.status_code == 403


# ── GET /analyses/{id} ────────────────────────────────────────────


async def test_get_analysis_returns_status(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """GET /analyses/{id} returns analysis status."""
    enquiry_id = await _create_enquiry(db_engine)

    # Create an analysis
    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    analysis_id = r1.json()["analysis_id"]

    # Get the analysis
    r2 = await app_client.get(
        f"/analyses/{analysis_id}",
        headers=reviewer_headers,
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["id"] == analysis_id
    assert data["enquiry_id"] == enquiry_id
    assert data["status"] == "pending"
    assert data["analysis_attempt"] == 1
    assert data["version"] == 1


async def test_get_analysis_not_found(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
) -> None:
    """404 when analysis doesn't exist."""
    response = await app_client.get(
        "/analyses/00000000-0000-0000-0000-000000000000",
        headers=reviewer_headers,
    )
    assert response.status_code == 404


async def test_get_analysis_invalid_uuid(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
) -> None:
    """404 for invalid UUID format."""
    response = await app_client.get(
        "/analyses/not-a-uuid",
        headers=reviewer_headers,
    )
    assert response.status_code == 404


# ── GET /jobs/{id} ────────────────────────────────────────────────


async def test_get_job_status(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """GET /jobs/{id} returns job status."""
    enquiry_id = await _create_enquiry(db_engine)

    r1 = await app_client.post(
        f"/enquiries/{enquiry_id}/analyses",
        json={"force": False},
        headers=reviewer_headers,
    )
    job_id = r1.json()["job_id"]

    r2 = await app_client.get(f"/jobs/{job_id}", headers=reviewer_headers)
    assert r2.status_code == 200
    data = r2.json()
    assert data["id"] == job_id
    assert data["status"] == "queued"
    assert data["kind"] == "analyse"


async def test_get_job_not_found(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
) -> None:
    """404 when job doesn't exist."""
    response = await app_client.get("/jobs/99999", headers=reviewer_headers)
    assert response.status_code == 404
