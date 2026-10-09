"""Real PostgreSQL dispatch boundaries with fake external providers/tools."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.enums import ActionStatus
from app.domain.models import Action, ActionAttempt, Analysis, Event, Job, SafetyVerdict
from app.jobs.handlers import AnalysisHandler, OutboxHandler
from app.jobs.queue import JobQueue, LeasedJob
from app.llm.provider import GenerateResult, Usage
from app.tools.protocol import ToolResult
from tests.integration.test_actions import _create_analysis, _create_enquiry, _seed_tool_config

pytestmark = pytest.mark.asyncio


async def test_analysis_dispatch_persists_and_does_not_repeat(db_engine):
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, "pending")
    router = SimpleNamespace(
        generate_structured=AsyncMock(
            return_value=GenerateResult(
                data={"summary": "A test enquiry", "category": "other", "priority": "medium"},
                usage=Usage(model="fake-model"),
            )
        )
    )
    handler = AnalysisHandler(router, MemorySaver())
    job = LeasedJob(1, "analyse", {"analysis_id": analysis_id}, 0, 5)
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessions() as db:
        await handler(job, db)
        await db.commit()
        await handler(job, db)
        await db.commit()
    async with sessions() as db:
        analysis = await db.get(Analysis, uuid.UUID(analysis_id))
        assert analysis.status == "pending_review"
        assert analysis.result["summary"] == "A test enquiry"
        assert analysis.model == "fake-model"
        assert len((await db.scalars(select(SafetyVerdict))).all()) >= 1
        assert (await db.scalar(select(Event))).payload["node"] == "await_review"
    router.generate_structured.assert_awaited_once()


@pytest.mark.parametrize(
    "outcome,retryable,expected",
    [
        ("sent", False, "sent"),
        ("failed", False, "failed"),
        ("failed", True, "pending"),
        ("unknown", False, "unknown"),
    ],
)
async def test_send_outcomes_commit_intent_and_log_attempt(
    db_engine,
    app_client,
    reviewer_headers,
    outcome,
    retryable,
    expected,
):
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack")
    response = await app_client.post(
        "/actions",
        headers=reviewer_headers,
        json={
            "analysis_id": analysis_id,
            "expected_analysis_version": 1,
            "destinations": ["slack"],
        },
    )
    assert response.status_code == 202
    action_id = uuid.UUID(response.json()["actions"][0]["id"])
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)

    async def execute(payload, key):
        # A separate connection can see intent before execute is called.
        async with sessions() as observer:
            action = await observer.get(Action, action_id)
            assert action.status == ActionStatus.sending
            assert action.attempts == 1
        return ToolResult(status=outcome, retryable=retryable)

    tool = SimpleNamespace(
        validate=AsyncMock(),
        execute=AsyncMock(side_effect=execute),
        reconcile=AsyncMock(return_value=None),
    )
    settings = SimpleNamespace(
        job_max_attempts=5,
        job_backoff_base_ms=500,
        job_backoff_max_ms=60000,
        action_sending_timeout_seconds=30,
    )
    queue = JobQueue("test")
    handler = OutboxHandler(queue, settings, lambda config: tool)
    async with sessions() as db:
        jobs = await queue.lease_batch(db, ["send_action"], 1, 60)
        assert len(jobs) == 1
        await db.commit()
        await handler(jobs[0], db)
        await queue.complete(db, jobs[0].id)
        await db.commit()
    async with sessions() as db:
        action = await db.get(Action, action_id)
        assert action.status.value == expected
        attempts = (await db.scalars(select(ActionAttempt))).all()
        assert len(attempts) == 1
        assert attempts[0].outcome == ("success" if outcome == "sent" else outcome)
        if expected != "pending":
            await handler(jobs[0], db)
            tool.execute.assert_awaited_once()
        else:
            assert len((await db.scalars(select(Job))).all()) == 2


async def test_crashed_send_swept_to_unknown_never_reexecuted(
    db_engine,
    app_client,
    reviewer_headers,
):
    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack")
    response = await app_client.post(
        "/actions",
        headers=reviewer_headers,
        json={
            "analysis_id": analysis_id,
            "expected_analysis_version": 1,
            "destinations": ["slack"],
        },
    )
    action_id = uuid.UUID(response.json()["actions"][0]["id"])
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    tool = SimpleNamespace(execute=AsyncMock(), reconcile=AsyncMock(return_value=None))
    handler = OutboxHandler(
        JobQueue("test"),
        SimpleNamespace(action_sending_timeout_seconds=30, job_max_attempts=5),
        lambda config: tool,
    )
    async with sessions() as db:
        action = await db.get(Action, action_id)
        action.status = ActionStatus.sending
        action.attempts = 1
        action.sending_started_at = datetime.now(UTC) - timedelta(seconds=90)
        await db.commit()
        job = LeasedJob(1, "send_action", {"action_id": str(action_id)}, 0, 5)
        await handler(job, db)
        assert await handler.sweep(db) == 1
        await db.commit()
        await handler(job, db)
        assert action.status == ActionStatus.unknown
        tool.execute.assert_not_awaited()


async def test_postgres_checkpoint_survives_handler_restart(db_engine):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.jobs.handlers import ReviewResumeHandler

    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id, "pending")
    router = SimpleNamespace(
        generate_structured=AsyncMock(
            return_value=GenerateResult(
                data={"summary": "Durable result", "category": "other", "priority": "medium"},
                usage=Usage(model="fake-model"),
            )
        )
    )
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    dsn = db_engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        async with sessions() as db:
            handler = AnalysisHandler(router, saver)
            job = LeasedJob(1, "analyse", {"analysis_id": analysis_id}, 0, 5)
            await handler(job, db)
            await db.rollback()  # crash after checkpoint, before result transaction commit
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        async with sessions() as db:
            await AnalysisHandler(router, saver)(job, db)
            await db.commit()
            analysis = await db.get(Analysis, uuid.UUID(analysis_id))
            assert analysis.result["summary"] == "Durable result"
            analysis.status = "approved"
            await db.commit()
            await ReviewResumeHandler(saver)(
                LeasedJob(
                    2,
                    "resume_review",
                    {
                        "analysis_id": analysis_id,
                        "decision": {"action": "approve"},
                    },
                    0,
                    5,
                ),
                db,
            )
            await db.commit()
            snapshot = await saver.aget_tuple({"configurable": {"thread_id": f"{enquiry_id}:1"}})
            assert snapshot.checkpoint["channel_values"]["review"]["status"] == "approved"
    router.generate_structured.assert_awaited_once()


async def test_legacy_pending_actions_get_one_dispatch_job(
    db_engine,
    app_client,
    reviewer_headers,
):
    from sqlalchemy import delete

    enquiry_id = await _create_enquiry(db_engine)
    analysis_id = await _create_analysis(db_engine, enquiry_id)
    await _seed_tool_config(db_engine, "slack")
    response = await app_client.post(
        "/actions",
        headers=reviewer_headers,
        json={
            "analysis_id": analysis_id,
            "expected_analysis_version": 1,
            "destinations": ["slack"],
        },
    )
    assert response.status_code == 202
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    handler = OutboxHandler(JobQueue("test"), SimpleNamespace())
    async with sessions() as db:
        await db.execute(delete(Job))  # model rows created before M9 dispatch was wired
        await db.commit()
        assert await handler.recover_pending(db) == 1
        await db.commit()
        assert await handler.recover_pending(db) == 0
        assert len((await db.scalars(select(Job))).all()) == 1
