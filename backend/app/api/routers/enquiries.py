"""Enquiries router — ingest + list + detail (§4.2 API table).

M2 implements:
  - POST /enquiries (ingest — A4: enquiries arrive via POST, matching EnquiryIn)
  - GET /enquiries (list with filters)
  - GET /enquiries/{id} (detail)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.enquiries import EnquiryIn, EnquiryListOut, EnquiryOut, enquiry_to_out
from app.deps import get_db_session
from app.domain.enums import Category, Decision, Priority, UserRole
from app.domain.models import Analysis, Enquiry, SafetyVerdict, User
from app.domain.warnings import apply_warnings, compute_warnings
from app.security.auth import require_role

router = APIRouter(tags=["enquiries"])

_viewer = require_role(UserRole.viewer)
_reviewer = require_role(UserRole.reviewer)


@router.post("/enquiries", status_code=status.HTTP_201_CREATED, response_model=EnquiryOut)
async def ingest_enquiry(
    body: EnquiryIn,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_reviewer),
) -> EnquiryOut:
    """Ingest a new enquiry (A4). Computes code warnings on insert."""
    existing = await db.get(Enquiry, body.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Enquiry {body.id} already exists.",
        )

    received_at = body.received_at or datetime.now(UTC)
    enquiry = Enquiry(
        id=body.id,
        name=body.name,
        email=body.email,
        company=body.company,
        status=body.status,
        message=body.message,
        received_at=received_at,
    )
    db.add(enquiry)
    await db.flush()

    warnings = await compute_warnings(
        db,
        enquiry_id=enquiry.id,
        name=enquiry.name,
        email=enquiry.email,
        company=enquiry.company,
    )
    apply_warnings(enquiry, warnings)
    await db.flush()

    return (await enrich_enquiries(db, [enquiry]))[0]


@router.get("/enquiries", response_model=EnquiryListOut)
async def list_enquiries(
    status_filter: str | None = Query(None, alias="status"),
    company: str | None = Query(None),
    priority: str | None = Query(None, description="Filter by latest analysis priority"),
    category: str | None = Query(None, description="Filter by latest analysis category"),
    safety_verdict: str | None = Query(None, description="Filter by latest safety verdict"),
    q: str | None = Query(None, description="Full-text search"),
    sort: str = Query("received_at", description="received_at | company"),
    cursor: str | None = Query(None, description="Keyset cursor (received_at ISO of last item)"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> EnquiryListOut:
    """List enquiries with filters, full-text search, and cursor pagination (§4.2)."""
    # Validate sort
    if sort not in ("received_at", "company"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sort value. Use 'received_at' or 'company'.",
        )

    # Validate enum filters
    if priority is not None:
        try:
            Priority(priority)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid priority: {priority}",
            ) from None
    if category is not None:
        try:
            Category(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category: {category}",
            ) from None
    if safety_verdict is not None:
        try:
            Decision(safety_verdict)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid safety_verdict: {safety_verdict}",
            ) from None

    # Parse cursor (keyset on received_at)
    cursor_dt: datetime | None = None
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format.",
            ) from None

    query = select(Enquiry)

    # Filters on enquiries table
    if status_filter:
        query = query.where(Enquiry.status == status_filter)
    if company:
        query = query.where(Enquiry.company == company)

    # Full-text search using the GIN index idx_enquiries_search (§4.1)
    if q:
        query = query.where(
            text(
                "to_tsvector('english', enquiries.name || ' ' || enquiries.company"
                " || ' ' || enquiries.message) @@ plainto_tsquery('english', :search_q)"
            ).bindparams(search_q=q)
        )

    # Filter by latest analysis priority (stored in analyses.result JSONB)
    if priority is not None:
        latest_priority = (
            select(Analysis.result["priority"].astext)
            .where(Analysis.enquiry_id == Enquiry.id)
            .order_by(Analysis.analysis_attempt.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(latest_priority == priority)

    # Filter by latest analysis category
    if category is not None:
        latest_category = (
            select(Analysis.result["category"].astext)
            .where(Analysis.enquiry_id == Enquiry.id)
            .order_by(Analysis.analysis_attempt.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(latest_category == category)

    # Filter by latest safety verdict (merged stage of latest analysis)
    if safety_verdict is not None:
        verdict_enum = Decision(safety_verdict)
        latest_verdict = (
            select(SafetyVerdict.decision)
            .join(Analysis, SafetyVerdict.analysis_id == Analysis.id)
            .where(
                Analysis.enquiry_id == Enquiry.id,
                SafetyVerdict.stage == "merged",
            )
            .order_by(Analysis.analysis_attempt.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(latest_verdict == verdict_enum)

    # Keyset cursor pagination on received_at
    if cursor_dt is not None:
        query = query.where(Enquiry.received_at < cursor_dt)

    # Sort
    if sort == "company":
        query = query.order_by(Enquiry.company, desc(Enquiry.received_at))
    else:
        query = query.order_by(desc(Enquiry.received_at))

    # Fetch limit + 1 to determine if there's a next page
    query = query.limit(limit + 1)
    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1].received_at.isoformat() if has_more and items else None

    return EnquiryListOut(
        items=await enrich_enquiries(db, items),
        next_cursor=next_cursor,
    )


@router.get("/enquiries/{id}", response_model=EnquiryOut)
async def get_enquiry(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(_viewer),
) -> EnquiryOut:
    enquiry = await db.get(Enquiry, id)
    if enquiry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That item couldn't be found — it may have been removed.",
        )
    return (await enrich_enquiries(db, [enquiry]))[0]


async def enrich_enquiries(db: AsyncSession, items: list[Enquiry]) -> list[EnquiryOut]:
    """Project latest analyses in two batch queries for the master-detail UI."""
    if not items:
        return []
    analyses = (
        await db.scalars(
            select(Analysis)
            .where(Analysis.enquiry_id.in_([e.id for e in items]))
            .distinct(Analysis.enquiry_id)
            .order_by(Analysis.enquiry_id, Analysis.analysis_attempt.desc())
        )
    ).all()
    by_enquiry = {a.enquiry_id: a for a in analyses}
    verdicts = (
        (
            await db.scalars(
                select(SafetyVerdict).where(SafetyVerdict.analysis_id.in_([a.id for a in analyses]))
            )
        ).all()
        if analyses
        else []
    )
    worst = {}
    for v in verdicts:
        if v.analysis_id not in worst or v.decision.severity > worst[v.analysis_id].severity:
            worst[v.analysis_id] = v.decision
    output = []
    for enquiry in items:
        dto = enquiry_to_out(enquiry)
        analysis = by_enquiry.get(enquiry.id)
        if analysis:
            dto.latest_analysis_id = str(analysis.id)
            dto.analysis_status = analysis.status
            dto.analysis_result = analysis.result
            verdict = worst.get(analysis.id)
            dto.safety_decision = verdict.value if verdict else None
        output.append(dto)
    return output
