"""Tests for the Postgres job queue (§3.4) and admin endpoints."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domain.models import Job
from app.jobs.queue import JobQueue

pytestmark = pytest.mark.asyncio


# ── JobQueue unit tests ────────────────────────────────────────────


async def test_enqueue_creates_queued_job(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "test_kind", {"data": "hello"})
        await session.commit()

        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.kind == "test_kind"
        assert job.status.value == "queued"
        assert job.payload == {"data": "hello"}


async def test_lease_batch_marks_running(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "lease_test", {"x": 1})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        leased = await queue.lease_batch(session, ["lease_test"], limit=10, lease_seconds=60)
        await session.commit()
        assert len(leased) == 1
        assert leased[0].id == job_id
        assert leased[0].kind == "lease_test"

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "running"
        assert job.locked_by == "test-worker"


async def test_lease_batch_skip_locked(db_engine: AsyncEngine) -> None:
    """A job already leased by another worker is not re-leased (SKIP LOCKED)."""
    queue1 = JobQueue(worker_id="worker-1")
    queue2 = JobQueue(worker_id="worker-2")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue1.enqueue(session, "skip_test", {"y": 2})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        leased1 = await queue1.lease_batch(session, ["skip_test"], limit=10, lease_seconds=60)
        await session.commit()
        assert len(leased1) == 1

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        leased2 = await queue2.lease_batch(session, ["skip_test"], limit=10, lease_seconds=60)
        await session.commit()
        assert len(leased2) == 0  # already locked by worker-1


async def test_complete_marks_succeeded(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "complete_test", {"z": 3})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["complete_test"], limit=1, lease_seconds=60)
        await queue.complete(session, job_id)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "succeeded"


async def test_fail_retryable_requeues_with_backoff(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "fail_retry_test", {"a": 1})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["fail_retry_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "transient error", retryable=True)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "queued"
        assert job.attempts == 1
        assert job.last_error == "transient error"


async def test_fail_not_retryable_dead_letters(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "fail_terminal_test", {"b": 2})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["fail_terminal_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "bad payload", retryable=False)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "dead_letter"
        assert job.dead_letter_reason == "bad payload"


async def test_fail_max_attempts_dead_letters(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job = Job(kind="max_attempt_test", payload={}, max_attempts=2)
        session.add(job)
        await session.flush()
        job_id = job.id
        await session.commit()

    # First failure (attempts 0 -> 1, retryable -> queued)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["max_attempt_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "error 1", retryable=True)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "queued"
        assert job.attempts == 1

    # Second failure (attempts 1 -> 2, >= max_attempts -> dead_letter)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["max_attempt_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "error 2", retryable=True)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "dead_letter"
        assert job.attempts == 2


async def test_reap_expired_leases(db_engine: AsyncEngine) -> None:
    """Expired running jobs are reaped back to queued."""
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "reap_test", {"c": 3})
        await session.commit()

    # Lease with a very short lease (1 second)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["reap_test"], limit=1, lease_seconds=1)
        await session.commit()

    # Wait for the lease to expire
    import asyncio

    await asyncio.sleep(2)

    # Reap should flip it back to queued
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        reaped = await queue.reap_expired_leases(session)
        await session.commit()
        assert reaped >= 1

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "queued"


async def test_replay_resets_dead_letter(db_engine: AsyncEngine) -> None:
    queue = JobQueue(worker_id="test-worker")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "replay_test", {"d": 4})
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await queue.lease_batch(session, ["replay_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "fatal", retryable=False)
        await session.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        applied = await queue.replay(session, job_id)
        await session.commit()
        assert applied is True

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        assert job.status.value == "queued"
        assert job.attempts == 0


# ── Admin endpoint tests ───────────────────────────────────────────


async def test_admin_list_dead_letter_jobs(
    app_client: httpx.AsyncClient,
    reviewer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Admin can list dead_letter jobs; non-admin gets 403."""
    queue = JobQueue(worker_id="test")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "dlq_test2", {"f": 6})
        await session.commit()
        await queue.lease_batch(session, ["dlq_test2"], limit=10, lease_seconds=60)
        await queue.fail(session, job_id, "fatal", retryable=False)
        await session.commit()

    # Non-admin (reviewer) gets 403
    resp = await app_client.get("/admin/jobs?status=dead_letter", headers=reviewer_headers)
    assert resp.status_code == 403

    # Admin gets the list
    resp = await app_client.get("/admin/jobs?status=dead_letter", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert all(item["status"] == "dead_letter" for item in items)


async def test_admin_replay_job(
    app_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Admin can replay a dead_letter job."""
    queue = JobQueue(worker_id="test")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "replay_api_test", {"g": 7})
        await session.commit()
        await queue.lease_batch(session, ["replay_api_test"], limit=1, lease_seconds=60)
        await queue.fail(session, job_id, "fatal", retryable=False)
        await session.commit()

    resp = await app_client.post(f"/admin/jobs/{job_id}/replay", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0


async def test_admin_replay_non_dead_letter_conflicts(
    app_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    db_engine: AsyncEngine,
) -> None:
    """Replaying a non-dead_letter job returns 409."""
    queue = JobQueue(worker_id="test")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job_id = await queue.enqueue(session, "replay_conflict_test", {"h": 8})
        await session.commit()

    resp = await app_client.post(f"/admin/jobs/{job_id}/replay", headers=admin_headers)
    assert resp.status_code == 409
