"""Postgres token-bucket rate limiter (§3.6).

Uses a Postgres row per bucket_key to track token count and last refill
time. Atomic UPDATE ensures correct concurrency across instances.

Schema (from 0002_full_ddl.py):
  bucket_key TEXT PRIMARY KEY
  tokens NUMERIC NOT NULL
  capacity NUMERIC NOT NULL
  refill_per_sec NUMERIC NOT NULL
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class RateLimitDecision:
    """Result of a rate limit check."""

    allowed: bool
    remaining: int
    retry_after_ms: int


class RateLimiter:
    """Token-bucket rate limiter backed by Postgres.

    Each bucket_key gets its own row. The bucket is created on first use
    with the configured capacity and refill rate.

    Args:
        capacity: maximum tokens in the bucket
        refill_rate: tokens added per second
    """

    def __init__(self, capacity: int = 60, refill_rate: float = 1.0) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate

    async def acquire(
        self, db: AsyncSession, scope: str, key: str, cost: int = 1
    ) -> RateLimitDecision:
        """Try to acquire `cost` tokens from the bucket.

        The `scope` is prepended to `key` to form the composite bucket_key,
        since the DB schema uses a single PK column.
        """
        bucket_key = f"{scope}:{key}"
        now = datetime.now(UTC)

        # Step 1: Upsert the bucket row (create with full capacity if new)
        await db.execute(
            text(
                "INSERT INTO rate_limit_buckets"
                " (bucket_key, tokens, capacity, refill_per_sec, updated_at)"
                " VALUES (:key, :capacity, :capacity, :refill_rate, :now)"
                " ON CONFLICT (bucket_key) DO NOTHING"
            ),
            {
                "key": bucket_key,
                "capacity": self._capacity,
                "refill_rate": self._refill_rate,
                "now": now,
            },
        )

        # Step 2: Atomically refill tokens, then check-and-deduct
        # Uses a CTE to compute refilled tokens, then conditionally deducts
        result = await db.execute(
            text(
                """
                WITH refilled AS (
                    SELECT
                        bucket_key,
                        LEAST(
                            capacity,
                            tokens + (refill_per_sec * EXTRACT(EPOCH FROM (:now - updated_at)))
                        ) AS refilled_tokens
                    FROM rate_limit_buckets
                    WHERE bucket_key = :key
                )
                UPDATE rate_limit_buckets AS rb
                SET tokens = CASE
                    WHEN r.refilled_tokens >= :cost
                    THEN r.refilled_tokens - :cost
                    ELSE r.refilled_tokens
                END,
                updated_at = :now
                FROM refilled r
                WHERE rb.bucket_key = r.bucket_key
                RETURNING
                    rb.tokens,
                    r.refilled_tokens AS tokens_before_deduct
                """
            ),
            {
                "key": bucket_key,
                "cost": cost,
                "now": now,
            },
        )

        row = result.fetchone()
        if row is None:
            return RateLimitDecision(
                allowed=True, remaining=self._capacity - cost, retry_after_ms=0
            )

        tokens_after = float(row.tokens)
        tokens_before_deduct = float(row.tokens_before_deduct)

        if tokens_before_deduct >= cost:
            return RateLimitDecision(
                allowed=True,
                remaining=int(tokens_after),
                retry_after_ms=0,
            )

        # Not enough tokens
        deficit = cost - tokens_before_deduct
        retry_after_ms = (
            int((deficit / self._refill_rate) * 1000) if self._refill_rate > 0 else 1000
        )
        return RateLimitDecision(
            allowed=False,
            remaining=int(tokens_after) if tokens_after > 0 else 0,
            retry_after_ms=retry_after_ms,
        )
