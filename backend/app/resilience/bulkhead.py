"""Bounded semaphore registry (§3.6 — bulkhead).

Per-provider/tool bounded semaphore to cap concurrent in-flight calls.
"""

from __future__ import annotations

import asyncio


class BulkheadRegistry:
    """Registry of named bounded semaphores for isolation."""

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get_or_create(self, key: str, max_concurrent: int = 10) -> asyncio.Semaphore:
        """Get or create a semaphore for a key."""
        if key not in self._semaphores:
            self._semaphores[key] = asyncio.Semaphore(max_concurrent)
        return self._semaphores[key]

    async def acquire(self, key: str, max_concurrent: int = 10) -> asyncio.Semaphore:
        """Acquire a semaphore for a key."""
        sem = self.get_or_create(key, max_concurrent)
        await sem.acquire()
        return sem

    def release(self, key: str) -> None:
        """Release a semaphore for a key."""
        sem = self._semaphores.get(key)
        if sem:
            sem.release()
