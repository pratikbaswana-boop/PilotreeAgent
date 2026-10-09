"""Admin endpoints — job + tool config management (§4.2 API table).

M3: dead-letter job viewing and replay.
M9: tool config management (GET /admin/tools, PUT /admin/tools/{key}).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db_session
from app.domain.enums import JobStatus, UserRole
from app.domain.models import Job, ToolConfig, User
from app.jobs.queue import JobQueue
from app.security.auth import require_role

router = APIRouter(prefix="/admin", tags=["admin"])
_admin = require_role(UserRole.admin)


@router.get("/jobs")
async def list_admin_jobs(
    job_status: str = Query("dead_letter", alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_admin),
) -> dict[str, object]:
    """List jobs filtered by status (default: dead_letter)."""
    try:
        JobStatus(job_status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {job_status}",
        ) from None

    query = (
        select(Job)
        .where(Job.status == JobStatus(job_status))
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    jobs = result.scalars().all()

    return {
        "items": [_job_to_dict(j) for j in jobs],
        "total": len(jobs),
    }


@router.post("/jobs/{id}/replay")
async def replay_job(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_admin),
) -> dict[str, object]:
    """Replay a dead_letter job: reset status='queued', attempts=0."""
    queue = JobQueue(worker_id="admin")
    applied = await queue.replay(db, id)
    if not applied:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not in dead_letter state or does not exist.",
        )
    job = await db.get(Job, id)
    assert job is not None  # applied=True guarantees it exists
    return _job_to_dict(job)


def _job_to_dict(job: Job) -> dict[str, object]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status.value,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "last_error": job.last_error,
        "dead_letter_reason": job.dead_letter_reason,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


# ── M9: Tool config management ───────────────────────────────────


@router.get("/tools")
async def list_tool_configs(
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_admin),
) -> list[dict[str, Any]]:
    """GET /admin/tools — list all tool configs (secrets redacted)."""
    result = await db.execute(select(ToolConfig))
    configs = result.scalars().all()
    return [_tool_config_to_dict(tc) for tc in configs]


@router.put("/tools/{key}")
async def update_tool_config(
    key: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_admin),
) -> dict[str, Any]:
    """PUT /admin/tools/{key} — update a tool config.

    Body: {enabled?: bool, config?: dict, credential?: str}
    """
    schemas: dict[str, Any] = {}
    from app.tools.linear import LinearConfig
    from app.tools.slack import SlackConfig

    schemas.update(slack=SlackConfig, linear=LinearConfig)
    if key == "sheets":
        raise HTTPException(
            status_code=409,
            detail=(
                "Google Sheets setup is unavailable until service-account "
                "authentication is enabled."
            ),
        )
    if key not in schemas:
        raise HTTPException(status_code=404, detail="Unknown destination.")

    config = await db.get(ToolConfig, key)
    incoming = body.get("config") or {}
    current = dict(config.config or {}) if config else {}
    secret_fields = {"slack": {"webhook_url"}, "linear": {"api_key"}}[key]
    merged = {
        **current,
        **{
            field: value
            for field, value in incoming.items()
            if value != "" or field not in secret_fields
        },
    }
    enabled = bool(body.get("enabled", config.enabled if config else True))
    if enabled:
        try:
            schemas[key].model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if config is None:
        config = ToolConfig(key=key, enabled=enabled, config=merged)
        db.add(config)
    else:
        config.enabled = enabled
        config.config = merged

    config.updated_at = datetime.now(UTC)
    await db.flush()

    return _tool_config_to_dict(config)


def _tool_config_to_dict(tc: ToolConfig) -> dict[str, Any]:
    """Convert a ToolConfig to a dict with secrets redacted."""
    secret_fields = {"webhook_url", "api_key", "token", "secret"}
    safe_config = {
        key: value for key, value in (tc.config or {}).items() if key not in secret_fields
    }
    return {
        "key": tc.key,
        "enabled": tc.enabled,
        "configured": bool(tc.config),
        "config": safe_config,
    }
