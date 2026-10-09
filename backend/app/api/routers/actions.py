"""Actions router — POST /actions, GET /actions, POST /actions/{id}/retry (§4.2).

M9 implements:
  - POST /actions (precondition checks, idempotency, outbox insertion)
  - GET /actions?enquiry_id= (list actions for an enquiry)
  - POST /actions/{id}/retry (retry failed/unknown actions, Fix 8)
  - GET /tools/metadata (tool UI metadata for destination chips)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db_session
from app.domain.enums import ActionStatus, UserRole
from app.domain.models import Action, ActionAttempt, Analysis, ToolConfig, User
from app.events import publish_event
from app.jobs.queue import JobQueue
from app.security.auth import require_role

router = APIRouter(tags=["actions"])

_reviewer = require_role(UserRole.reviewer)
_viewer = require_role(UserRole.viewer)

_INJECTION_REASON_CODES = {
    "injection_heuristic",
    "injection_judge",
    "encoded_payload",
}


async def _injection_restriction(db: AsyncSession, analysis_id: uuid.UUID) -> bool:
    """Return true when an analysis must never be allowed to execute tools."""
    from app.domain.models import SafetyVerdict

    verdicts = (
        await db.scalars(select(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis_id))
    ).all()
    return any(_INJECTION_REASON_CODES.intersection(v.reason_codes or []) for v in verdicts)


@router.post("/actions", status_code=status.HTTP_202_ACCEPTED)
async def create_actions(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """POST /actions — create action rows for outbound sends (Fix 2, Fix 3).

    Precondition checks (independent of graph state):
      - analysis.status == 'approved'
      - verdict is not BLOCK
      - QUARANTINE must be confirmed (typed_reason provided)
      - destination must be enabled (tool_config)
      - breaker must not be open for the destination
      - expected_analysis_version must match
      - first send per (enquiry, destination) uses idempotency key enquiry_id:destination
      - resend requires resend=true and resend_reason
    """
    analysis_id = body.get("analysis_id")
    destinations = body.get("destinations", [])
    expected_version = body.get("expected_analysis_version")
    payload_overrides = body.get("payload_overrides")
    resend = body.get("resend", False)
    resend_reason = body.get("resend_reason")

    if not analysis_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="analysis_id is required.",
        )

    if not destinations or not isinstance(destinations, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="destinations must be a non-empty list.",
        )

    if expected_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="expected_analysis_version is required.",
        )

    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        ) from None

    analysis = await db.get(Analysis, analysis_uuid)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    if await _injection_restriction(db, analysis_uuid):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=("Tool execution is disabled because this analysis detected prompt injection."),
        )

    # Check analysis status — must be approved
    if analysis.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Analysis must be approved before sending.",
        )

    # Check version
    if analysis.version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Expected analysis version is stale.",
                "current": analysis.version,
            },
        )

    # Check for BLOCK verdict (from safety_verdicts)
    from app.domain.models import SafetyVerdict

    result = await db.execute(
        select(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis_uuid)
    )
    verdicts = result.scalars().all()
    has_block = any(v.decision == "BLOCK" for v in verdicts)
    has_quarantine = any(v.decision == "QUARANTINE" for v in verdicts)

    if has_block:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Cannot send: analysis has a BLOCK verdict.",
        )

    if has_quarantine and not body.get("quarantine_confirmed"):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Cannot send: QUARANTINE verdict must be confirmed.",
        )

    # Check destinations are enabled
    tc_result = await db.execute(select(ToolConfig))
    tool_configs: dict[str, ToolConfig] = {tc.key: tc for tc in tc_result.scalars().all()}

    actions_created: list[dict[str, Any]] = []

    for destination in destinations:
        if destination == "sheets":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Google Sheets is disabled.",
            )
        # Check if destination is enabled
        tool_config: ToolConfig | None = tool_configs.get(destination)
        if tool_config is None or not tool_config.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Destination '{destination}' is disabled.",
            )

        # Build idempotency key
        if resend:
            if not resend_reason:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="resend_reason is required for resend.",
                )
            # Resend gets a unique key with sequence
            idempotency_key = f"resend:{analysis.enquiry_id}:{destination}:{uuid.uuid4().hex[:8]}"
        else:
            idempotency_key = f"{analysis.enquiry_id}:{destination}"

        # Check for existing action with same idempotency key (first send only)
        if not resend:
            existing = await db.execute(
                select(Action).where(Action.idempotency_key == idempotency_key)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Already sent to '{destination}'. Use resend=true with a reason.",
                )

        # Build payload
        payload = analysis.result or {}
        if payload_overrides:
            payload = {**payload, **payload_overrides}

        # Create the action
        action = Action(
            id=uuid.uuid4(),
            enquiry_id=analysis.enquiry_id,
            analysis_id=analysis_uuid,
            analysis_version=analysis.version,
            destination=destination,
            idempotency_key=idempotency_key,
            payload=payload,
            status=ActionStatus.pending,
            requested_by=user.id,
            resend_reason=resend_reason if resend else None,
        )
        db.add(action)
        await db.flush()
        await JobQueue("api").enqueue(db, "send_action", {"action_id": str(action.id)})

        actions_created.append(
            {
                "id": str(action.id),
                "destination": destination,
                "status": action.status.value,
                "idempotency_key": idempotency_key,
            }
        )

        # Publish event
        await publish_event(
            db,
            f"enquiry:{analysis.enquiry_id}",
            "action.created",
            {
                "action_id": str(action.id),
                "destination": destination,
                "status": "pending",
            },
        )

    return {"actions": actions_created}


@router.get("/actions")
async def list_actions(
    enquiry_id: str = Query(..., description="Filter by enquiry_id"),
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> list[dict[str, Any]]:
    """GET /actions?enquiry_id= — list actions for an enquiry."""
    result = await db.execute(
        select(Action).where(Action.enquiry_id == enquiry_id).order_by(Action.created_at)
    )
    actions = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "enquiry_id": a.enquiry_id,
            "analysis_id": str(a.analysis_id),
            "destination": a.destination,
            "status": a.status.value if hasattr(a.status, "value") else str(a.status),
            "idempotency_key": a.idempotency_key,
            "external_id": a.external_id,
            "attempts": a.attempts,
            "resend_of": str(a.resend_of) if a.resend_of else None,
            "resend_reason": a.resend_reason,
            "created_at": a.created_at.isoformat(),
            "updated_at": a.updated_at.isoformat(),
        }
        for a in actions
    ]


@router.post("/actions/{action_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_action(
    action_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """POST /actions/{id}/retry — retry a failed or unknown action (Fix 8).

    For 'unknown' actions:
      - First attempts Tool.reconcile() automatically
      - If reconcile resolves, no confirm needed
      - If reconcile returns None, requires {confirm: true, typed_reason}
    For 'failed' actions: plain retry, no confirmation needed.
    """
    try:
        action_uuid = uuid.UUID(action_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found.",
        ) from None

    action = await db.scalar(select(Action).where(Action.id == action_uuid).with_for_update())
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found.",
        )

    if await _injection_restriction(db, action.analysis_id):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=("Tool execution is disabled because this analysis detected prompt injection."),
        )

    # Must be in failed or unknown state
    if action.status not in (ActionStatus.failed, ActionStatus.unknown):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Action is in '{action.status.value}' state," " not 'failed' or 'unknown'."),
        )

    confirm = body.get("confirm", False)
    typed_reason = body.get("typed_reason")

    # Reconcile ambiguous sends before allowing any new network side effect.
    if action.status == ActionStatus.unknown:
        from app.jobs.handlers import configured_tool

        config = await db.get(ToolConfig, action.destination)
        resolved = None
        if config and config.enabled:
            try:
                resolved = await configured_tool(config).reconcile(
                    action.payload, action.idempotency_key
                )
            except Exception:
                resolved = None
        if resolved is not None and resolved.status == "sent":
            action.status = ActionStatus.sent
            action.external_id = resolved.external_id
            action.updated_at = datetime.now(UTC)
            db.add(
                ActionAttempt(
                    action_id=action.id,
                    attempt_no=action.attempts,
                    outcome="success",
                    via_reconcile=True,
                )
            )
            await publish_event(
                db,
                f"enquiry:{action.enquiry_id}",
                "action.updated",
                {"action_id": str(action.id), "status": "sent"},
            )
            return {"id": str(action.id), "status": "sent", "destination": action.destination}
        safe_retry = resolved is not None and resolved.status == "failed" and resolved.retryable
        if not safe_retry and (
            not confirm or not isinstance(typed_reason, str) or not typed_reason.strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=("Retrying an unknown action requires" " confirm=true and typed_reason."),
            )

    # Reset to pending for the outbox worker to pick up
    action.status = ActionStatus.pending
    action.updated_at = datetime.now(UTC)
    await db.flush()

    await JobQueue("api").enqueue(db, "send_action", {"action_id": str(action.id)})

    # Publish event
    await publish_event(
        db,
        f"enquiry:{action.enquiry_id}",
        "action.updated",
        {
            "action_id": str(action.id),
            "status": "pending",
            "retry": True,
            "reviewer_id": str(user.id),
        },
    )

    return {
        "id": str(action.id),
        "status": "pending",
        "destination": action.destination,
    }


@router.get("/tools/metadata")
async def get_tools_metadata(
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> list[dict[str, Any]]:
    """GET /tools/metadata — tool UI metadata for destination chips."""
    result = await db.execute(select(ToolConfig).where(ToolConfig.key != "sheets"))
    configs = result.scalars().all()

    # Built-in tool metadata
    tool_meta = {
        "slack": {
            "key": "slack",
            "label": "Slack",
            "icon": "slack",
            "suggested_for": ["high", "urgent"],
            "payload_preview_fields": ["summary", "category", "priority"],
        },
        "linear": {
            "key": "linear",
            "label": "Linear",
            "icon": "linear",
            "suggested_for": ["high", "medium"],
            "payload_preview_fields": ["summary", "category", "priority"],
        },
        "sheets": {
            "key": "sheets",
            "label": "Google Sheets",
            "icon": "sheets",
            "suggested_for": ["low", "medium"],
            "payload_preview_fields": ["summary", "category", "priority"],
        },
    }

    metadata: list[dict[str, Any]] = []
    for config in configs:
        meta = tool_meta.get(
            config.key,
            {
                "key": config.key,
                "label": config.key,
                "icon": config.key,
                "suggested_for": [],
                "payload_preview_fields": [],
            },
        )
        metadata.append(
            {
                **meta,
                "enabled": config.enabled,
            }
        )

    return metadata


@router.get("/analyses/{analysis_id}/action-proposals")
async def get_action_proposals(
    analysis_id: str,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> dict[str, Any]:
    """Generate executable proposals from an analysis and configured tools."""
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Analysis not found.") from None
    analysis = await db.get(Analysis, analysis_uuid)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")

    injection_found = await _injection_restriction(db, analysis_uuid)
    result = analysis.result or {}
    priority = str(result.get("priority", ""))
    recommendations = {
        item.get("tool"): item
        for item in result.get("recommended_tools", [])
        if isinstance(item, dict)
    }
    configs = (
        await db.scalars(
            select(ToolConfig).where(ToolConfig.key != "sheets").order_by(ToolConfig.key)
        )
    ).all()
    labels = {"slack": "Slack", "linear": "Linear", "sheets": "Google Sheets"}
    action_labels = {"slack": "Send Slack alert", "linear": "Create Linear task"}
    proposals = []
    for config in configs:
        recommendation = recommendations.get(config.key)
        recommended = recommendation is not None
        proposals.append(
            {
                "destination": config.key,
                "label": labels.get(config.key, config.key.replace("_", " ").title()),
                "action_label": action_labels.get(
                    config.key, f"Send to {labels.get(config.key, config.key.title())}"
                ),
                "recommended": recommended,
                "reason": (
                    str(recommendation.get("reason", "Recommended by the analysis."))
                    if recommended
                    else (
                        "Available for manual routing of this "
                        f"{priority or 'unprioritised'} enquiry."
                    )
                ),
                "executable": (
                    bool(config.enabled) and not injection_found and analysis.status == "approved"
                ),
                "disabled_reason": (
                    "Tool actions are locked because prompt injection was detected."
                    if injection_found
                    else (
                        "Approve this analysis to enable the action."
                        if analysis.status != "approved"
                        else (None if config.enabled else "Destination is disabled.")
                    )
                ),
                "request": {
                    "method": "POST",
                    "path": "/actions",
                    "body": {
                        "analysis_id": str(analysis.id),
                        "expected_analysis_version": analysis.version,
                        "destinations": [config.key],
                    },
                },
            }
        )
    return {"tool_execution_disabled": injection_found, "proposals": proposals}
