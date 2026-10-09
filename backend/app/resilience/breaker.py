"""Postgres-backed circuit breaker (§3.8).

State machine: closed -> open (after N failures) -> half_open (after cooldown)
-> closed (on success) or open (on failure in half_open).

Each instance caches breaker_state in-process with a 2s TTL (ADR).
Writes (record_failure/record_success) always hit Postgres so all
instances converge within the TTL window.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import BreakerState

logger = logging.getLogger(__name__)

# Breaker states
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class BreakerSnapshot:
    """Snapshot of a breaker's state."""

    state: str
    failure_count: int
    opened_at: datetime | None
    updated_at: datetime


class CircuitBreaker:
    """Postgres-backed circuit breaker with in-process TTL cache.

    Args:
        failure_threshold: number of failures before opening
        cooldown_seconds: time before transitioning from open to half_open
        cache_ttl_seconds: in-process cache TTL (2s per ADR)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: int = 60,
        cache_ttl_seconds: int = 2,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[BreakerSnapshot, float]] = {}

    async def allow(self, db: AsyncSession, key: str) -> bool:
        """Check if a request is allowed (closed or half_open)."""
        snapshot = await self._get_state(db, key)

        if snapshot.state == CLOSED:
            return True

        if snapshot.state == OPEN:
            # Check if cooldown has elapsed -> transition to half_open
            if snapshot.opened_at and datetime.now(UTC) - snapshot.opened_at >= timedelta(
                seconds=self._cooldown_seconds
            ):
                await self._transition(db, key, HALF_OPEN)
                return True
            return False

        if snapshot.state == HALF_OPEN:
            return True

        return True  # unknown state, allow

    async def record_success(self, db: AsyncSession, key: str) -> None:
        """Record a successful call — closes the breaker."""
        snapshot = await self._get_state(db, key)
        if snapshot.state in (OPEN, HALF_OPEN):
            await self._transition(db, key, CLOSED)
        self._invalidate_cache(key)

    async def record_failure(self, db: AsyncSession, key: str) -> None:
        """Record a failed call — may open the breaker."""
        snapshot = await self._get_state(db, key)

        if snapshot.state == CLOSED:
            new_failures = snapshot.failure_count + 1
            if new_failures >= self._failure_threshold:
                await self._transition(db, key, OPEN, failure_count=new_failures)
            else:
                # Just increment the failure count
                now = datetime.now(UTC)
                await db.execute(
                    update(BreakerState)
                    .where(BreakerState.breaker_key == key)
                    .values(failure_count=new_failures, updated_at=now)
                )
        elif snapshot.state == HALF_OPEN:
            # A failure in half_open reopens the breaker
            await self._transition(db, key, OPEN, failure_count=snapshot.failure_count + 1)

        self._invalidate_cache(key)

    async def state(self, db: AsyncSession, key: str) -> BreakerSnapshot:
        """Get the current breaker state (from cache or DB)."""
        return await self._get_state(db, key)

    async def _get_state(self, db: AsyncSession, key: str) -> BreakerSnapshot:
        """Get breaker state, using in-process cache with TTL."""
        cached = self._cache.get(key)
        if cached:
            snapshot, ts = cached
            if time.time() - ts < self._cache_ttl_seconds:
                return snapshot

        # Read from DB
        result = await db.execute(select(BreakerState).where(BreakerState.breaker_key == key))
        row = result.scalar_one_or_none()

        if row is None:
            # Create a new closed breaker
            snapshot = BreakerSnapshot(
                state=CLOSED,
                failure_count=0,
                opened_at=None,
                updated_at=datetime.now(UTC),
            )
            await self._ensure_row(db, key)
        else:
            snapshot = BreakerSnapshot(
                state=row.state,
                failure_count=row.failure_count,
                opened_at=row.opened_at,
                updated_at=row.updated_at,
            )

        self._cache[key] = (snapshot, time.time())
        return snapshot

    async def _transition(
        self,
        db: AsyncSession,
        key: str,
        new_state: str,
        *,
        failure_count: int = 0,
    ) -> None:
        """Transition the breaker to a new state."""
        now = datetime.now(UTC)
        opened_at = now if new_state == OPEN else None

        await db.execute(
            update(BreakerState)
            .where(BreakerState.breaker_key == key)
            .values(
                state=new_state,
                failure_count=failure_count if new_state == OPEN else 0,
                opened_at=opened_at,
                updated_at=now,
            )
        )
        logger.info("breaker.transition", extra={"key": key, "new_state": new_state})

    async def _ensure_row(self, db: AsyncSession, key: str) -> None:
        """Ensure a breaker row exists (INSERT if not)."""
        now = datetime.now(UTC)
        await db.execute(
            text(
                "INSERT INTO breaker_state"
                " (breaker_key, state, failure_count,"
                " opened_at, updated_at)"
                " VALUES (:key, 'closed', 0, NULL, :now)"
                " ON CONFLICT (breaker_key) DO NOTHING"
            ),
            {"key": key, "now": now},
        )

    def _invalidate_cache(self, key: str) -> None:
        """Invalidate the in-process cache for a key."""
        self._cache.pop(key, None)
