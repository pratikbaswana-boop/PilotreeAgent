"""Analysis router — analysis, review, and edit endpoints (§4.2).

M7 implements:
  - POST /enquiries/{id}/analyses (single-flight, advisory lock, reuse/force)
  - GET /analyses/{id} (analysis status + result + safety verdicts + review prompt)
  - GET /jobs/{id} (job status)

M8 implements:
  - PATCH /analyses/{id} (edit analysis fields with If-Match optimistic concurrency)
  - POST /analyses/{id}/resume-review (approve/edit_and_approve/reject/override_block)
  - POST /enquiries/{id}/claim (soft claim with TTL)
  - POST /enquiries/{id}/release (release claim)
  - POST /reviews (dismiss_false_positive — not tied to graph resume)
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.analyses import (
    AnalysisPatchRequest,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    JobStatusResponse,
    ReviewDecisionRequest,
)
from app.deps import get_db_session
from app.domain.enums import UserRole
from app.domain.models import Analysis, Enquiry, Event, Job, Review, SafetyVerdict, User
from app.events import publish_event
from app.graph.nodes.await_review import _build_review_prompt
from app.graph.nodes.merge_verdicts import escalate
from app.jobs.queue import JobQueue
from app.security.auth import require_role

router = APIRouter(tags=["analyses"])

_reviewer = require_role(UserRole.reviewer)
_viewer = require_role(UserRole.viewer)

# Prompt/schema versions (would be config-driven in production)
PROMPT_VERSION = "v2"
SCHEMA_VERSION = "v1"


def _compute_input_hash(enquiry: Enquiry) -> str:
    """Compute a stable hash of the enquiry content for single-flight dedup."""
    content = (
        f"{enquiry.id}|{enquiry.name}|{enquiry.company}|{enquiry.email}"
        f"|{enquiry.message}|{enquiry.status}"
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _advisory_lock_key(enquiry_id: str) -> int:
    """Convert enquiry_id to a stable int for pg_advisory_xact_lock."""
    # Use first 8 bytes of a hash as the lock key
    h = hashlib.sha256(enquiry_id.encode()).digest()
    return int.from_bytes(h[:8], byteorder="big", signed=True)


@router.post(
    "/enquiries/{enquiry_id}/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisResponse,
)
async def start_analysis(
    enquiry_id: str,
    body: AnalysisRequest,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_reviewer),
) -> AnalysisResponse:
    """Start or reuse an analysis for an enquiry (single-flight, Fix 5).

    Uses pg_advisory_xact_lock(enquiry_id) for the whole critical section:
    1. Check for an in-flight analysis (pending/in_progress) with same input_hash.
    2. If !force, check for a reusable completed analysis with same input_hash.
    3. Otherwise, allocate a new attempt number and create a new analysis + job.
    """
    # Verify enquiry exists
    enquiry = await db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enquiry not found.",
        )

    input_hash = _compute_input_hash(enquiry)
    lock_key = _advisory_lock_key(enquiry_id)

    # Take an advisory lock scoped to this enquiry (Fix 5)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    # 1. Check for an in-flight analysis with the same input_hash
    result = await db.execute(
        select(Analysis).where(
            Analysis.enquiry_id == enquiry_id,
            Analysis.input_hash == input_hash,
            Analysis.prompt_version == PROMPT_VERSION,
            Analysis.status.in_(["pending", "in_progress"]),
        )
    )
    in_flight = result.scalar_one_or_none()

    if in_flight is not None:
        # Single-flight: return the existing in-flight analysis
        return AnalysisResponse(
            job_id=in_flight.job_id,
            analysis_id=str(in_flight.id),
            attached=True,
            reused=False,
        )

    # 2. If !force, check for a reusable completed analysis
    if not body.force:
        result = await db.execute(
            select(Analysis)
            .where(
                Analysis.enquiry_id == enquiry_id,
                Analysis.input_hash == input_hash,
                Analysis.prompt_version == PROMPT_VERSION,
                Analysis.status.in_(["pending_review", "approved", "rejected"]),
            )
            .order_by(desc(Analysis.analysis_attempt))
            .limit(1)
        )
        reusable = result.scalar_one_or_none()

        if reusable is not None:
            return AnalysisResponse(
                job_id=None,
                analysis_id=str(reusable.id),
                attached=True,
                reused=True,
            )

    # 3. Allocate a new attempt number
    result = await db.execute(
        select(Analysis)
        .where(Analysis.enquiry_id == enquiry_id)
        .order_by(desc(Analysis.analysis_attempt))
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    next_attempt = (latest.analysis_attempt + 1) if latest else 1

    # Create the analysis row
    analysis = Analysis(
        id=uuid.uuid4(),
        enquiry_id=enquiry_id,
        analysis_attempt=next_attempt,
        input_hash=input_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        status="pending",
    )
    db.add(analysis)
    await db.flush()

    # Create a job row for the worker to pick up
    job = Job(
        kind="analyse",
        payload={
            "enquiry_id": enquiry_id,
            "analysis_id": str(analysis.id),
            "input_hash": input_hash,
        },
        priority=0,
        max_attempts=3,
    )
    db.add(job)
    await db.flush()

    # Link the job to the analysis
    analysis.job_id = job.id
    await db.flush()

    return AnalysisResponse(
        job_id=job.id,
        analysis_id=str(analysis.id),
        attached=False,
        reused=False,
    )


@router.get("/analyses/{analysis_id}", response_model=AnalysisStatus)
async def get_analysis(
    analysis_id: str,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> AnalysisStatus:
    """Get analysis status, result, safety verdicts, and review prompt."""
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
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

    # Fetch safety verdicts
    result = await db.execute(
        select(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis_uuid)
    )
    verdicts = result.scalars().all()

    progress = await db.scalar(
        select(Event)
        .where(
            Event.topic == f"enquiry:{analysis.enquiry_id}",
            Event.type == "analysis.updated",
            Event.payload["analysis_id"].astext == str(analysis.id),
            Event.payload.has_key("phase"),
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    phase = progress.payload.get("phase", "queued") if progress else "queued"
    node = progress.payload.get("node", "queued") if progress else "queued"
    if analysis.status in {"pending_review", "approved", "rejected"}:
        phase, node = "awaiting_review", "await_review"
    return AnalysisStatus(
        phase=phase,
        node=node,
        id=str(analysis.id),
        enquiry_id=analysis.enquiry_id,
        analysis_attempt=analysis.analysis_attempt,
        status=analysis.status,
        result=analysis.result,
        llm_usage=analysis.usage,
        model=analysis.model,
        version=analysis.version,
        safety_verdicts=[
            {
                "stage": v.stage,
                "decision": v.decision,
                "reason_codes": v.reason_codes,
                "evidence": v.evidence,
                "severity": v.severity,
            }
            for v in verdicts
        ],
        job_id=analysis.job_id,
        review_prompt=_build_review_prompt(
            {
                "enquiry_id": str(analysis.id),
                "validated_result": analysis.result,
                "final_verdict": escalate(
                    [
                        {
                            "decision": v.decision,
                            "reason_codes": v.reason_codes,
                            "evidence": v.evidence,
                            "severity": v.severity,
                        }
                        for v in verdicts
                    ]
                ),
            }
        ).model_dump()
        if analysis.status == "pending_review"
        else None,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: int,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_reviewer),
) -> JobStatusResponse:
    """Get job status (Fix 6)."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    return JobStatusResponse(
        id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        kind=job.kind,
        attempts=job.attempts,
        last_error=job.last_error,
    )


# ── M8: Review & Edit API ─────────────────────────────────────────


# Claim TTL (soft claim, ADR-9)
CLAIM_TTL_MINUTES = 30


@router.post("/enquiries/{enquiry_id}/claim")
async def claim_enquiry(
    enquiry_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """Soft-claim an enquiry (ADR-9). Claims are not hard DB locks —
    they expire after CLAIM_TTL_MINUTES and can be overridden."""
    enquiry = await db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enquiry not found.",
        )

    # Check if already claimed by someone else (and not expired)
    now = datetime.now(UTC)
    if (
        enquiry.claimed_by is not None
        and enquiry.claim_expires_at is not None
        and enquiry.claim_expires_at > now
        and enquiry.claimed_by != user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Enquiry is already claimed by another user.",
        )

    enquiry.claimed_by = user.id
    enquiry.claimed_at = now
    enquiry.claim_expires_at = now + timedelta(minutes=CLAIM_TTL_MINUTES)
    await db.flush()

    return {
        "claimed_by": str(user.id),
        "claim_expires_at": enquiry.claim_expires_at.isoformat(),
    }


@router.post("/enquiries/{enquiry_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release_enquiry(
    enquiry_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> Response:
    """Release a soft claim. Reviewers can only release their own claims;
    admins can release any claim."""
    enquiry = await db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enquiry not found.",
        )

    if enquiry.claimed_by is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)  # nothing to release

    if enquiry.claimed_by != user.id and user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only release your own claim.",
        )

    enquiry.claimed_by = None
    enquiry.claimed_at = None
    enquiry.claim_expires_at = None
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/analyses/{analysis_id}")
async def patch_analysis(
    analysis_id: str,
    body: AnalysisPatchRequest,
    if_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """Edit analysis result fields (§4.2, ADR-9).

    Uses If-Match header for optimistic concurrency (version check).
    Only allowed when analysis.status == 'pending_review'.
    BLOCK verdict analyses are locked (no edits) unless admin override.
    """
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        ) from None

    analysis = await db.scalar(
        select(Analysis).where(Analysis.id == analysis_uuid).with_for_update()
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    # Check status — must be pending_review
    if analysis.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Analysis is not in pending_review state.",
        )

    # Optimistic concurrency check
    if if_match is not None:
        try:
            expected_version = int(if_match)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid If-Match header.",
            ) from None
        if analysis.version != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Version conflict.",
                    "current": analysis.version,
                },
            )

    verdicts = (
        await db.scalars(select(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis_uuid))
    ).all()
    if any(v.decision == "BLOCK" for v in verdicts):
        raise HTTPException(status_code=423, detail="BLOCK analyses require an admin override.")
    # Apply edits to the result
    if analysis.result is None:
        analysis.result = {}

    analysis.result = dict(analysis.result)
    updates = body.model_dump(exclude_none=True)
    diff: dict[str, Any] = {}
    for key, value in updates.items():
        old_value = analysis.result.get(key)
        if old_value != value:
            diff[key] = {"old": old_value, "new": value}
            analysis.result[key] = value

    if diff:
        analysis.version += 1
        analysis.updated_at = datetime.now(UTC)
        await db.flush()

        # Publish event
        await publish_event(
            db,
            f"enquiry:{analysis.enquiry_id}",
            "analysis.updated",
            {
                "analysis_id": str(analysis.id),
                "version": analysis.version,
                "edited_fields": list(diff.keys()),
            },
        )

    return {
        "id": str(analysis.id),
        "version": analysis.version,
        "result": analysis.result,
    }


@router.post("/analyses/{analysis_id}/resume-review")
async def resume_review(
    analysis_id: str,
    body: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """Resume a paused analysis review (§4.2, Fix 1, Fix 10).

    Actions:
      - approve: approve the analysis as-is
      - edit_and_approve: apply edited_fields and approve
      - reject: reject the analysis
      - override_block: admin-only, override a BLOCK verdict

    Validates:
      - expected_analysis_version matches current version
      - override_block requires admin role
      - typed_reason required for certain actions (reject, override_block, quarantine confirm)
      - edited_fields required for edit_and_approve and override_block
    """
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        ) from None

    analysis = await db.scalar(
        select(Analysis).where(Analysis.id == analysis_uuid).with_for_update()
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    # Must be in pending_review state
    if analysis.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Analysis is in '{analysis.status}' state, not 'pending_review'.",
        )

    # Optimistic concurrency check
    if body.expected_analysis_version != analysis.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Expected analysis version is stale.",
                "current": analysis.version,
            },
        )

    # Validate action
    valid_actions = {"approve", "edit_and_approve", "reject", "override_block"}
    if body.action not in valid_actions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid action. Must be one of: {valid_actions}",
        )

    # override_block is admin-only (Fix 10)
    if body.action == "override_block" and user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="override_block requires admin role.",
        )

    # edited_fields required for edit_and_approve and override_block
    if body.action in ("edit_and_approve", "override_block"):
        if not body.edited_fields or len(body.edited_fields) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="edited_fields is required for this action.",
            )

    # typed_reason required for reject (and override_block)
    if body.action in ("reject", "override_block"):
        if not body.typed_reason or not body.typed_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="typed_reason is required for this action.",
            )

    verdicts = (
        await db.scalars(select(SafetyVerdict).where(SafetyVerdict.analysis_id == analysis_uuid))
    ).all()
    if any(v.decision == "BLOCK" for v in verdicts) and body.action not in {
        "reject",
        "override_block",
    }:
        raise HTTPException(status_code=423, detail="BLOCK analyses require an admin override.")
    if any(v.decision == "QUARANTINE" for v in verdicts) and body.action != "reject":
        if not body.typed_reason or len(body.typed_reason.strip()) < 10:
            raise HTTPException(
                status_code=422,
                detail="Quarantine confirmation needs a reason of at least 10 characters.",
            )
    # Apply edited fields if provided
    diff: dict[str, Any] | None = None
    if body.edited_fields:
        analysis.result = dict(analysis.result or {})
        diff = {}
        for key, value in body.edited_fields.items():
            old_value = analysis.result.get(key)
            if old_value != value:
                diff[key] = {"old": old_value, "new": value}
                analysis.result[key] = value

    # Determine new status
    if body.action in ("approve", "edit_and_approve", "override_block"):
        new_status = "approved"
    else:
        new_status = "rejected"

    # Update analysis
    analysis.status = new_status
    analysis.version += 1
    analysis.updated_at = datetime.now(UTC)
    await db.flush()

    await JobQueue("api").enqueue(
        db,
        "resume_review",
        {
            "analysis_id": str(analysis.id),
            "decision": {**body.model_dump(), "reviewer_id": str(user.id)},
        },
    )

    # Create review record
    review = Review(
        id=uuid.uuid4(),
        analysis_id=analysis_uuid,
        reviewer_id=user.id,
        action=body.action,
        typed_reason=body.typed_reason,
        diff=diff,
    )
    db.add(review)
    await db.flush()

    # Publish event
    await publish_event(
        db,
        f"enquiry:{analysis.enquiry_id}",
        "analysis.reviewed",
        {
            "analysis_id": str(analysis.id),
            "action": body.action,
            "status": new_status,
            "version": analysis.version,
            "reviewer_id": str(user.id),
        },
    )

    return {
        "id": str(analysis.id),
        "status": new_status,
        "version": analysis.version,
        "review_id": str(review.id),
    }


@router.post("/reviews")
async def create_review(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(_reviewer),
) -> dict[str, Any]:
    """POST /reviews — dismiss_false_positive (§4.2, Fix 1).

    The only review action NOT tied to resuming the graph.
    Used to mark a safety verdict as a false positive without changing
    the analysis status.
    """
    analysis_id = body.get("analysis_id")
    action = body.get("action")
    typed_reason = body.get("typed_reason")

    if not analysis_id or not isinstance(analysis_id, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="analysis_id is required.",
        )

    if action != "dismiss_false_positive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only 'dismiss_false_positive' action is allowed via POST /reviews.",
        )

    if not typed_reason or not isinstance(typed_reason, str) or not typed_reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="typed_reason is required.",
        )

    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        ) from None

    analysis = await db.scalar(
        select(Analysis).where(Analysis.id == analysis_uuid).with_for_update()
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    # Only allowed on a non-BLOCK, already-decided analysis
    if analysis.status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reviews can only be added to already-decided analyses.",
        )

    review = Review(
        id=uuid.uuid4(),
        analysis_id=analysis_uuid,
        reviewer_id=user.id,
        action=action,
        typed_reason=typed_reason,
        diff=None,
    )
    db.add(review)
    await db.flush()

    return {
        "id": str(review.id),
        "analysis_id": str(analysis_uuid),
        "action": action,
        "reviewer_id": str(user.id),
    }
