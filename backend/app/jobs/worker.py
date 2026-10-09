"""Worker process entrypoint (§3.4).

Runs WORKER_CONCURRENCY async tasks pulling from the job queue, dispatching
by kind to registered handlers. A background timer reaps expired leases for
crash recovery.

Run as: ``python -m app.jobs.worker``  (same container image, different command).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.infra.db import create_db
from app.jobs.queue import JobQueue, LeasedJob

logger = logging.getLogger(__name__)

# Handler type: async function taking (LeasedJob, AsyncSession) -> None
JobHandler = Callable[[LeasedJob, Any], Awaitable[None]]


class Worker:
    """Job queue worker with configurable concurrency and dispatch by kind."""

    def __init__(
        self,
        queue: JobQueue,
        db_manager: Any,
        concurrency: int = 8,
        lease_seconds: int = 60,
        heartbeat_seconds: int = 20,
        backoff_base_ms: int = 500,
        backoff_max_ms: int = 60000,
        reap_interval_seconds: int = 30,
        sweep_handler: Any = None,
    ) -> None:
        self._queue = queue
        self._db_manager = db_manager
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._backoff_base_ms = backoff_base_ms
        self._backoff_max_ms = backoff_max_ms
        self._reap_interval_seconds = reap_interval_seconds
        self._handlers: dict[str, JobHandler] = {}
        self._sweep_handler = sweep_handler
        self._running = False

    def register_handler(self, kind: str, handler: JobHandler) -> None:
        """Register a handler for a job kind."""
        self._handlers[kind] = handler

    async def run(self) -> None:
        """Main worker loop: spawn concurrency tasks + reaper, run until stopped."""
        self._running = True
        tasks = [asyncio.create_task(self._worker_loop()) for _ in range(self._concurrency)]
        reaper = asyncio.create_task(self._reaper_loop())
        all_tasks = [*tasks, reaper]

        try:
            await asyncio.gather(*all_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    async def _worker_loop(self) -> None:
        """Single worker task: lease -> dispatch -> complete/fail, repeat."""
        while self._running:
            try:
                async with self._db_manager.session_factory() as session:
                    leased = await self._queue.lease_batch(
                        session,
                        kinds=list(self._handlers.keys()),
                        limit=1,
                        lease_seconds=self._lease_seconds,
                    )
                    if not leased:
                        await session.commit()
                        await asyncio.sleep(1)
                        continue

                    await session.commit()  # Make lease visible before dispatch/heartbeat.
                    job = leased[0]
                    handler = self._handlers.get(job.kind)
                    if handler is None:
                        await self._queue.fail(
                            session,
                            job.id,
                            f"No handler for kind '{job.kind}'",
                            retryable=False,
                            backoff_base_ms=self._backoff_base_ms,
                            backoff_max_ms=self._backoff_max_ms,
                        )
                        await session.commit()
                        continue

                    # Start heartbeat task while the job runs
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(job.id))
                    try:
                        await handler(job, session)
                        await self._queue.complete(session, job.id)
                    except Exception as exc:
                        await session.rollback()
                        await self._queue.fail(
                            session,
                            job.id,
                            str(exc),
                            retryable=True,
                            backoff_base_ms=self._backoff_base_ms,
                            backoff_max_ms=self._backoff_max_ms,
                        )
                        logger.exception("job.failed", extra={"job_id": job.id, "kind": job.kind})
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass

                    await session.commit()
            except Exception:
                logger.exception("worker_loop_error")
                await asyncio.sleep(5)

    async def _heartbeat_loop(self, job_id: int) -> None:
        """Send heartbeats while the job is running (must be < lease/3)."""
        while self._running:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                async with self._db_manager.session_factory() as session:
                    await self._queue.heartbeat(session, job_id, self._lease_seconds)
                    await session.commit()
            except Exception:
                logger.exception("heartbeat_failed", extra={"job_id": job_id})

    async def _reaper_loop(self) -> None:
        """Periodically reap expired leases for crash recovery."""
        while self._running:
            await asyncio.sleep(self._reap_interval_seconds)
            try:
                async with self._db_manager.session_factory() as session:
                    if self._sweep_handler is not None:
                        await self._sweep_handler(session)
                    reaped = await self._queue.reap_expired_leases(session)
                    if reaped > 0:
                        logger.info("leases.reaped", extra={"count": reaped})
                    await session.commit()
            except Exception:
                logger.exception("reaper_error")


async def main() -> None:
    """Worker entrypoint — run with: python -m app.jobs.worker"""
    settings = get_settings()
    db_manager = create_db(settings)
    queue = JobQueue(worker_id=f"worker-{uuid.uuid4().hex[:8]}")

    worker = Worker(
        queue=queue,
        db_manager=db_manager,
        concurrency=settings.worker_concurrency,
        lease_seconds=settings.job_lease_seconds,
        heartbeat_seconds=settings.job_heartbeat_seconds,
        backoff_base_ms=settings.job_backoff_base_ms,
        backoff_max_ms=settings.job_backoff_max_ms,
    )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    from sqlalchemy.engine import make_url

    from app.jobs.handlers import AnalysisHandler, OutboxHandler, ReviewResumeHandler
    from app.llm.gemini import GeminiProvider
    from app.llm.registry import ProviderConfig, ProviderRegistry
    from app.llm.router import ProviderRouter
    from app.resilience.breaker import CircuitBreaker
    from app.resilience.bulkhead import BulkheadRegistry
    from app.resilience.rate_limit import RateLimiter

    provider_config = ProviderConfig(
        model=settings.gemini_model_primary,
        api_key=settings.gemini_api_key,
        timeout_seconds=settings.llm_read_timeout_ms // 1000,
    )
    registry = ProviderRegistry()
    registry.register(GeminiProvider(provider_config), priority=1, config=provider_config)
    router = ProviderRouter(
        registry,
        CircuitBreaker(),
        BulkheadRegistry(),
        RateLimiter(
            capacity=settings.rate_limit_gemini_rps, refill_rate=settings.rate_limit_gemini_rps
        ),
    )
    dsn = make_url(settings.database_session_pool_url).set(drivername="postgresql")
    checkpoint_pool = AsyncConnectionPool(
        dsn.render_as_string(hide_password=False),
        open=False,
        min_size=1,
        max_size=settings.database_session_pool_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await checkpoint_pool.open()
    saver = AsyncPostgresSaver(checkpoint_pool)
    await saver.setup()
    outbox = OutboxHandler(queue, settings)
    worker.register_handler("analyse", AnalysisHandler(router, saver, db_manager.session_factory))
    worker.register_handler("send_action", outbox)
    worker.register_handler("resume_review", ReviewResumeHandler(saver))
    worker._sweep_handler = outbox.sweep
    async with db_manager.session_factory() as session:
        await outbox.recover_pending(session)
        await session.commit()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("worker.shutdown_signal")
        worker.stop()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("worker.started", extra={"concurrency": settings.worker_concurrency})

    run_task = asyncio.create_task(worker.run())
    await stop_event.wait()
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    await checkpoint_pool.close()
    await db_manager.dispose()
    logger.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
