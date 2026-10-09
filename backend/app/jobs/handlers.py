"""Worker handlers. External sends cross a committed `sending` boundary."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import Text, delete, select

from app.domain.enums import ActionStatus
from app.domain.models import (
    Action,
    ActionAttempt,
    Analysis,
    Enquiry,
    Job,
    SafetyVerdict,
    ToolConfig,
)
from app.events import publish_event
from app.graph.builder import build_enquiry_triage_graph
from app.graph.nodes.llm_call import llm_call
from app.graph.runner import make_thread_config, make_thread_id
from app.jobs.queue import JobQueue, LeasedJob
from app.resilience.breaker import CircuitBreaker
from app.resilience.bulkhead import BulkheadRegistry
from app.resilience.rate_limit import RateLimiter
from app.tools.protocol import ToolResult


def configured_tool(config: ToolConfig) -> Any:
    from app.tools.linear import LinearTool
    from app.tools.sheets import SheetsTool
    from app.tools.slack import SlackTool

    implementations = {"slack": SlackTool, "linear": LinearTool, "sheets": SheetsTool}
    implementation = implementations[config.key]
    return implementation(implementation.config_schema.model_validate(config.config))


class AnalysisHandler:
    def __init__(self, router: Any, saver: Any, session_factory: Any = None) -> None:
        self.session_factory = session_factory
        self.router = router
        self.saver = saver

    async def __call__(self, job: LeasedJob, db: Any) -> None:
        analysis = await db.scalar(
            select(Analysis)
            .where(Analysis.id == uuid.UUID(job.payload["analysis_id"]))
            .with_for_update()
        )
        if analysis is None or analysis.status not in {"pending", "in_progress"}:
            return
        enquiry = await db.get(Enquiry, analysis.enquiry_id)
        if enquiry is None:
            raise ValueError("Analysis enquiry is missing")

        async def progress(node: str) -> None:
            if self.session_factory is None or node == "await_review":
                return
            phase = {
                "ingest_validate": "screening",
                "all_screens": "screening",
                "merge_verdicts": "screening",
                "llm_call": "analysing",
                "validate_output": "validating",
            }[node]
            async with self.session_factory() as events_db:
                await publish_event(
                    events_db,
                    f"enquiry:{analysis.enquiry_id}",
                    "analysis.updated",
                    {
                        "analysis_id": str(analysis.id),
                        "node": node,
                        "phase": phase,
                    },
                )
                await events_db.commit()

        graph = build_enquiry_triage_graph(
            checkpointer=self.saver,
            llm_node=partial(llm_call, router=self.router, db=db),
            progress=progress,
        )
        config = make_thread_config(make_thread_id(analysis.enquiry_id, analysis.analysis_attempt))
        snapshot = await graph.aget_state(config)
        if snapshot.values:
            # An interrupt may have been checkpointed just before a process crash.
            interrupted = any(task.interrupts for task in snapshot.tasks)
            state = snapshot.values if interrupted else await graph.ainvoke(None, config)
        else:
            state = await graph.ainvoke(
                {
                    "enquiry_id": enquiry.id,
                    "analysis_attempt": analysis.analysis_attempt,
                    "prompt_version": analysis.prompt_version,
                    "schema_version": analysis.schema_version,
                    "raw": {
                        "message": enquiry.message,
                        "metadata": {
                            "customer_name": enquiry.name,
                            "customer_email": enquiry.email,
                            "status": enquiry.status,
                        },
                    },
                    "node_versions": {},
                },
                config,
            )
        analysis.result = state.get("validated_result")
        analysis.llm_raw_result = state.get("llm_result")
        analysis.usage = state.get("llm_usage")
        analysis.model = (analysis.usage or {}).get("model")
        analysis.node_versions = state.get("node_versions", {})
        analysis.status = "pending_review"
        analysis.updated_at = datetime.now(UTC)
        await db.execute(delete(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis.id))
        verdicts = list(state.get("screen_verdicts", {}).values())
        verdicts.append(state.get("final_verdict"))
        for verdict in verdicts:
            if verdict:
                db.add(
                    SafetyVerdict(
                        analysis_id=analysis.id,
                        stage=verdict.get("stage", "final"),
                        decision=verdict["decision"],
                        reason_codes=verdict.get("reason_codes", []),
                        evidence=verdict.get("evidence", []),
                        severity=verdict.get("severity", 0),
                    )
                )
        await publish_event(
            db,
            f"enquiry:{analysis.enquiry_id}",
            "analysis.updated",
            {
                "analysis_id": str(analysis.id),
                "status": "pending_review",
                "phase": "awaiting_review",
                "node": "await_review",
                "result": analysis.result,
                "verdict": state.get("final_verdict"),
                "version": analysis.version,
            },
        )


class OutboxHandler:
    def __init__(self, queue: JobQueue, settings: Any, tool_factory: Any = configured_tool) -> None:
        self.queue = queue
        self.settings = settings
        self.tool_factory = tool_factory
        self.breaker = CircuitBreaker()
        self.bulkheads = BulkheadRegistry()
        self.limiters = {
            key: RateLimiter(
                capacity=getattr(settings, f"rate_limit_{key}_rps", 1),
                refill_rate=getattr(settings, f"rate_limit_{key}_rps", 1),
            )
            for key in ("slack", "linear", "sheets")
        }

    async def __call__(self, job: LeasedJob, db: Any) -> None:
        action = await db.scalar(
            select(Action).where(Action.id == uuid.UUID(job.payload["action_id"])).with_for_update()
        )
        # Reaped jobs must NEVER repeat an ambiguous send.
        if (
            action is None
            or action.status != ActionStatus.pending
            or action.run_at > datetime.now(UTC)
        ):
            return
        config = await db.get(ToolConfig, action.destination)
        try:
            if config is None or not config.enabled:
                raise ValueError("Destination is disabled")
            tool = self.tool_factory(config)
            await tool.validate(action.payload)
        except (ValueError, KeyError):
            action.attempts += 1
            await self.record(
                db,
                action,
                ToolResult(status="failed", error="Invalid tool configuration or payload"),
            )
            return
        breaker_key = f"tool:{action.destination}"
        allowed = await self.breaker.allow(db, breaker_key)
        rate = await self.limiters[action.destination].acquire(db, "tool", action.destination)
        if not allowed or not rate.allowed:
            action.run_at = datetime.now(UTC) + timedelta(
                milliseconds=max(rate.retry_after_ms, 1000)
            )
            await self.queue.enqueue(
                db, "send_action", {"action_id": str(action.id)}, run_at=action.run_at
            )
            return
        await self.bulkheads.acquire(
            breaker_key, getattr(self.settings, f"bulkhead_{action.destination}_concurrency", 3)
        )
        action.status = ActionStatus.sending
        action.sending_started_at = datetime.now(UTC)
        action.updated_at = action.sending_started_at
        action.attempts += 1
        attempt_no = action.attempts
        started = time.monotonic()
        try:
            await db.commit()  # Persist intent BEFORE any network request.
            result = await tool.execute(action.payload, action.idempotency_key)
        except Exception:
            # Unexpected failure after entering execute cannot prove no bytes were sent.
            result = ToolResult(
                status="unknown", error="Tool execution outcome could not be confirmed"
            )
        finally:
            self.bulkheads.release(breaker_key)
        via_reconcile = False
        if result.status == "unknown":
            try:
                resolved = await tool.reconcile(action.payload, action.idempotency_key)
                if resolved is not None:
                    result, via_reconcile = resolved, True
            except Exception:
                result = ToolResult(status="unknown", error="Reconciliation unavailable")
        await db.refresh(action, with_for_update=True)
        if action.status != ActionStatus.sending or action.attempts != attempt_no:
            return  # Sweeper or reviewer already owns the outcome.
        if result.status not in {"sent", "failed", "unknown"}:
            result = ToolResult(status="unknown", error="Invalid tool outcome")
        if result.status == "sent":
            await self.breaker.record_success(db, breaker_key)
        elif result.retryable:
            await self.breaker.record_failure(db, breaker_key)
        retry = (
            result.status == "failed"
            and result.retryable
            and action.attempts < self.settings.job_max_attempts
        )
        await self.record(
            db, action, result, via_reconcile, int((time.monotonic() - started) * 1000), retry
        )
        if retry:
            delay = min(
                self.settings.job_backoff_max_ms,
                self.settings.job_backoff_base_ms * 2 ** (action.attempts - 1),
            )
            delay = max(delay, (result.retry_after_seconds or 0) * 1000)
            action.run_at = datetime.now(UTC) + timedelta(milliseconds=delay)
            await self.queue.enqueue(
                db, "send_action", {"action_id": str(action.id)}, run_at=action.run_at
            )

    async def record(
        self,
        db: Any,
        action: Action,
        result: ToolResult,
        via_reconcile: bool = False,
        latency_ms: int | None = None,
        retry: bool = False,
    ) -> None:
        action.status = ActionStatus.pending if retry else ActionStatus(result.status)
        action.external_id = result.external_id
        action.updated_at = datetime.now(UTC)
        action.sending_started_at = None
        db.add(
            ActionAttempt(
                action_id=action.id,
                attempt_no=action.attempts,
                outcome="success" if result.status == "sent" else result.status,
                via_reconcile=via_reconcile,
                error=result.error,
                latency_ms=latency_ms,
            )
        )
        await publish_event(
            db,
            f"enquiry:{action.enquiry_id}",
            "action.updated",
            {
                "action_id": str(action.id),
                "status": action.status.value,
                "destination": action.destination,
            },
        )

    async def recover_pending(self, db: Any) -> int:
        """Enqueue legacy pending rows which predate job creation in the API."""
        active_job = (
            select(Job.id)
            .where(
                Job.kind == "send_action",
                Job.status.in_(["queued", "running"]),
                Job.payload["action_id"].astext == Action.id.cast(Text),
            )
            .exists()
        )
        actions = (
            await db.scalars(
                select(Action)
                .where(
                    Action.status == ActionStatus.pending,
                    ~active_job,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for action in actions:
            await self.queue.enqueue(
                db, "send_action", {"action_id": str(action.id)}, run_at=action.run_at
            )
        return len(actions)

    async def sweep(self, db: Any) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.action_sending_timeout_seconds)
        actions = (
            await db.scalars(
                select(Action)
                .where(
                    Action.status == ActionStatus.sending,
                    Action.sending_started_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for action in actions:
            result = ToolResult(status="unknown", error="Worker send timed out")
            config = await db.get(ToolConfig, action.destination)
            resolved = None
            if config is not None and config.enabled:
                try:
                    resolved = await self.tool_factory(config).reconcile(
                        action.payload, action.idempotency_key
                    )
                except Exception:
                    resolved = None
            if resolved is not None:
                result = resolved
            retry = (
                result.status == "failed"
                and result.retryable
                and action.attempts < self.settings.job_max_attempts
            )
            await self.record(db, action, result, via_reconcile=resolved is not None, retry=retry)
            if retry:
                await self.queue.enqueue(db, "send_action", {"action_id": str(action.id)})
        return len(actions)


class ReviewResumeHandler:
    """Finish a checkpoint after the review API commits its durable decision."""

    def __init__(self, saver: Any) -> None:
        self.saver = saver

    async def __call__(self, job: LeasedJob, db: Any) -> None:
        from langgraph.types import Command

        analysis = await db.scalar(
            select(Analysis)
            .where(Analysis.id == uuid.UUID(job.payload["analysis_id"]))
            .with_for_update()
        )
        if analysis is None or analysis.status not in {"approved", "rejected"}:
            return
        graph = build_enquiry_triage_graph(checkpointer=self.saver)
        config = make_thread_config(make_thread_id(analysis.enquiry_id, analysis.analysis_attempt))
        snapshot = await graph.aget_state(config)
        if not snapshot.values or not snapshot.next:
            return
        await graph.aupdate_state(
            config, {"validated_result": analysis.result}, as_node="validate_output"
        )
        await graph.ainvoke(Command(resume=job.payload["decision"]), config)
