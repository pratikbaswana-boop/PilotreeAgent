"""Postgres-backed job queue (§3.4).

Custom queue using FOR UPDATE SKIP LOCKED for leasing, with heartbeats,
dead-letter state, and crash recovery via lease expiry reaping.

Lease/heartbeat algorithm per DESIGN.md §3.4:
  - enqueue: INSERT row with status='queued'
  - lease_batch: SELECT FOR UPDATE SKIP LOCKED, UPDATE to 'running'
  - heartbeat: extend lease_expires_at
  - complete: status='succeeded' (Fix 6)
  - fail: retryable -> re-queue with backoff; not retryable or max_attempts -> dead_letter
  - reap_expired_leases: flip expired 'running' back to 'queued' for crash recovery
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import JobStatus
from app.domain.models import Job

# Type alias — JobId is the bigint PK.
JobId = int


@dataclass(frozen=True)
class LeasedJob:
    """A job leased by a worker, carrying the data needed to execute it."""

    id: JobId
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


class JobQueue:
    """Postgres-backed job queue with FOR UPDATE SKIP LOCKED leasing."""

    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id

    # ── Enqueue ─────────────────────────────────────────────────────

    async def enqueue(
        self,
        db: AsyncSession,
        kind: str,
        payload: dict[str, Any],
        *,
        run_at: datetime | None = None,
        priority: int = 0,
    ) -> JobId:
        """Insert a new job row with status='queued'."""
        job = Job(
            kind=kind,
            payload=payload,
            status=JobStatus.queued,
            priority=priority,
            run_at=run_at or datetime.now(UTC),
        )
        db.add(job)
        await db.flush()
        return job.id

    # ── Lease ────────────────────────────────────────────────────────

    async def lease_batch(
        self,
        db: AsyncSession,
        kinds: list[str],
        limit: int,
        lease_seconds: int,
    ) -> list[LeasedJob]:
        """Lease up to `limit` jobs of the given kinds using SKIP LOCKED.

        SELECT ... FOR UPDATE SKIP LOCKED, then UPDATE to 'running'.
        """
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)

        # Single-statement atomic lease: UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)
        # This avoids holding a cursor open across the UPDATE.
        lease_sql = text(
            """
            UPDATE jobs SET
                status = 'running',
                locked_by = :worker_id,
                lease_expires_at = :lease_expires_at,
                heartbeat_at = :now,
                updated_at = :now
            WHERE id IN (
                SELECT id FROM jobs
                WHERE status = 'queued'
                  AND run_at <= :now
                  AND kind = ANY(:kinds)
                ORDER BY priority DESC, run_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            RETURNING id, kind, payload, attempts, max_attempts
            """
        )
        result = await db.execute(
            lease_sql,
            {
                "worker_id": self._worker_id,
                "lease_expires_at": lease_expires_at,
                "now": now,
                "kinds": list(kinds),
                "limit": limit,
            },
        )
        rows = result.fetchall()
        return [
            LeasedJob(
                id=row.id,
                kind=row.kind,
                payload=row.payload,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
            )
            for row in rows
        ]

    # ── Heartbeat ────────────────────────────────────────────────────

    async def heartbeat(self, db: AsyncSession, job_id: JobId, lease_seconds: int) -> None:
        """Extend the lease for a running job."""
        now = datetime.now(UTC)
        await db.execute(
            update(Job)
            .where(
                Job.id == job_id, Job.status == JobStatus.running, Job.locked_by == self._worker_id
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
                updated_at=now,
            )
        )

    # ── Complete ────────────────────────────────────────────────────

    async def complete(self, db: AsyncSession, job_id: JobId) -> None:
        """Mark a job as succeeded (Fix 6: 'succeeded' status added)."""
        now = datetime.now(UTC)
        await db.execute(
            update(Job).where(Job.id == job_id).values(status=JobStatus.succeeded, updated_at=now)
        )

    # ── Fail ────────────────────────────────────────────────────────

    async def fail(
        self,
        db: AsyncSession,
        job_id: JobId,
        error: str,
        *,
        retryable: bool,
        backoff_base_ms: int = 500,
        backoff_max_ms: int = 60000,
    ) -> None:
        """Handle job failure.

        retryable -> status='queued', run_at=now()+backoff(attempts), attempts+=1
                     unless attempts>=max_attempts -> status='dead_letter'
        not retryable -> status='dead_letter' immediately
        """
        now = datetime.now(UTC)

        # Fetch current job to check attempts
        result = await db.execute(select(Job.attempts, Job.max_attempts).where(Job.id == job_id))
        row = result.fetchone()
        if row is None:
            return

        new_attempts = row.attempts + 1

        if not retryable or new_attempts >= row.max_attempts:
            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.dead_letter,
                    attempts=new_attempts,
                    last_error=error[:2000],
                    dead_letter_reason=error[:2000],
                    updated_at=now,
                )
            )
        else:
            # Exponential backoff with full jitter
            backoff_ms = min(
                backoff_base_ms * (2 ** (new_attempts - 1)),
                backoff_max_ms,
            )
            jitter = random.uniform(0, backoff_ms)  # noqa: S311
            run_at = now + timedelta(milliseconds=jitter)
            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.queued,
                    attempts=new_attempts,
                    last_error=error[:2000],
                    run_at=run_at,
                    locked_by=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=now,
                )
            )

    # ── Reap expired leases ─────────────────────────────────────────

    async def reap_expired_leases(self, db: AsyncSession) -> int:
        """Flip expired 'running' jobs back to 'queued' for crash recovery.

        Returns the number of reaped jobs.
        """
        now = datetime.now(UTC)
        result = await db.execute(
            update(Job)
            .where(
                Job.status == JobStatus.running,
                Job.lease_expires_at < now,
            )
            .values(
                status=JobStatus.queued,
                locked_by=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=now,
            )
            .returning(Job.id)
        )
        reaped = result.fetchall()
        return len(reaped)

    # ── Replay (admin) ──────────────────────────────────────────────

    async def replay(self, db: AsyncSession, job_id: JobId) -> bool:
        """Reset a dead_letter job to queued (admin action). Returns True if applied."""
        now = datetime.now(UTC)
        result = await db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.dead_letter,
            )
            .values(
                status=JobStatus.queued,
                attempts=0,
                last_error=None,
                dead_letter_reason=None,
                run_at=now,
                locked_by=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=now,
            )
            .returning(Job.id)
        )
        return result.fetchone() is not None
