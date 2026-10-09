"""Tests for enquiry ingest, list, and detail endpoints."""

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_ingest_enquiry(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    resp = await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-001",
            "name": "Alice Smith",
            "email": "alice@acme.com",
            "company": "Acme",
            "status": "open",
            "message": "My delivery hasn't arrived yet.",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "SHIP-001"
    assert body["name"] == "Alice Smith"
    assert body["warning_missing_name"] is False
    assert body["warning_invalid_email"] is False


async def test_ingest_missing_name_warning(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    resp = await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-002",
            "name": "",
            "email": "bob@acme.com",
            "company": "Acme",
            "status": "open",
            "message": "Question about my order.",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["warning_missing_name"] is True


async def test_ingest_invalid_email_warning(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    resp = await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-003",
            "name": "Carol",
            "email": "not-an-email",
            "company": "Beta Corp",
            "status": "pending",
            "message": "Need help.",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["warning_invalid_email"] is True


async def test_ingest_duplicate_id_conflict(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    payload = {
        "id": "SHIP-DUP",
        "name": "Dave",
        "email": "dave@corp.com",
        "company": "Corp",
        "status": "open",
        "message": "First message.",
    }
    resp1 = await app_client.post("/enquiries", json=payload, headers=reviewer_headers)
    assert resp1.status_code == 201

    resp2 = await app_client.post("/enquiries", json=payload, headers=reviewer_headers)
    assert resp2.status_code == 409


async def test_ingest_requires_auth(app_client: httpx.AsyncClient) -> None:
    resp = await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-X",
            "name": "",
            "email": "x@y.com",
            "company": "C",
            "status": "s",
            "message": "m",
        },
    )
    assert resp.status_code == 401


async def test_list_enquiries(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    for i in range(3):
        await app_client.post(
            "/enquiries",
            json={
                "id": f"SHIP-L{i}",
                "name": f"User {i}",
                "email": f"user{i}@example.com",
                "company": "Example",
                "status": "open",
                "message": f"Message {i}.",
            },
            headers=reviewer_headers,
        )
    resp = await app_client.get("/enquiries", headers=reviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 3
    assert "next_cursor" in body


async def test_list_cursor_pagination(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    """Cursor pagination returns pages and a next_cursor when more exist."""
    for i in range(5):
        await app_client.post(
            "/enquiries",
            json={
                "id": f"SHIP-P{i}",
                "name": f"User {i}",
                "email": f"userp{i}@example.com",
                "company": "Example",
                "status": "open",
                "message": f"Page test {i}.",
            },
            headers=reviewer_headers,
        )
    # Page 1: limit=2
    resp1 = await app_client.get("/enquiries?limit=2", headers=reviewer_headers)
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    # Page 2: use cursor from page 1
    resp2 = await app_client.get(
        "/enquiries",
        params={"limit": 2, "cursor": body1["next_cursor"]},
        headers=reviewer_headers,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["items"]) >= 1
    # No overlap between pages
    page1_ids = {item["id"] for item in body1["items"]}
    page2_ids = {item["id"] for item in body2["items"]}
    assert page1_ids.isdisjoint(page2_ids)


async def test_list_with_filter(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-F1",
            "name": "A",
            "email": "a@c.com",
            "company": "CompA",
            "status": "open",
            "message": "m",
        },
        headers=reviewer_headers,
    )
    await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-F2",
            "name": "B",
            "email": "b@d.com",
            "company": "CompB",
            "status": "closed",
            "message": "m",
        },
        headers=reviewer_headers,
    )
    resp = await app_client.get("/enquiries?status=open", headers=reviewer_headers)
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] == "open"


async def test_get_enquiry_detail(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-G1",
            "name": "Greg",
            "email": "g@h.com",
            "company": "H",
            "status": "open",
            "message": "Detail test.",
        },
        headers=reviewer_headers,
    )
    resp = await app_client.get("/enquiries/SHIP-G1", headers=reviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == "SHIP-G1"


async def test_get_enquiry_not_found(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    resp = await app_client.get("/enquiries/NOTEXIST", headers=reviewer_headers)
    assert resp.status_code == 404


async def test_message_too_long_rejected(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    resp = await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-LONG",
            "name": "X",
            "email": "x@y.com",
            "company": "C",
            "status": "s",
            "message": "A" * 5001,
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 422


async def test_fulltext_search(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    """FTS via GIN index (§4.1 idx_enquiries_search) returns matching enquiries."""
    await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-FTS1",
            "name": "Alice",
            "email": "alice@acme.com",
            "company": "Acme",
            "status": "open",
            "message": "My shipment of frozen goods was delayed.",
        },
        headers=reviewer_headers,
    )
    await app_client.post(
        "/enquiries",
        json={
            "id": "SHIP-FTS2",
            "name": "Bob",
            "email": "bob@beta.com",
            "company": "Beta",
            "status": "open",
            "message": "I need a quote for monthly logistics services.",
        },
        headers=reviewer_headers,
    )
    resp = await app_client.get("/enquiries?q=shipment", headers=reviewer_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(item["id"] == "SHIP-FTS1" for item in items)
    assert all(item["id"] != "SHIP-FTS2" for item in items)


async def test_invalid_sort_rejected(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    """Invalid sort value returns 400 (§4.3 error taxonomy)."""
    resp = await app_client.get("/enquiries?sort=invalid", headers=reviewer_headers)
    assert resp.status_code == 400


async def test_invalid_priority_filter_rejected(
    app_client: httpx.AsyncClient, reviewer_headers: dict[str, str]
) -> None:
    """Invalid priority filter value returns 400."""
    resp = await app_client.get("/enquiries?priority=urgent", headers=reviewer_headers)
    assert resp.status_code == 400


async def test_domain_warning_requires_prior_company_domain(app_client, reviewer_headers):
    async def ingest(id, email):
        response = await app_client.post(
            "/enquiries",
            headers=reviewer_headers,
            json={
                "id": id,
                "name": "Reviewer",
                "email": email,
                "company": "Domain Test",
                "status": "new",
                "message": "Please quote for a delivery.",
            },
        )
        assert response.status_code == 201
        return response.json()["warning_domain_mismatch"]

    assert await ingest("DOMAIN-1", "one@example.com") is False
    assert await ingest("DOMAIN-2", "two@EXAMPLE.COM") is False
    assert await ingest("DOMAIN-3", "three@different.com") is True
