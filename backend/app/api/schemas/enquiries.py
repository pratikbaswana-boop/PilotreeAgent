"""Pydantic request/response schemas for the enquiries API (§4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EnquiryIn(BaseModel):
    """Ingest payload — matches the `enquiries` table's external fields."""

    id: str = Field(..., max_length=200, description="External id from ingest")
    name: str = Field("", max_length=200)
    email: str = Field(..., max_length=200)
    company: str = Field(..., max_length=200)
    status: str = Field(..., max_length=100, description="Source status")
    message: str = Field(..., max_length=5000)
    received_at: datetime | None = Field(None, description="Defaults to now() if omitted")


class EnquiryOut(BaseModel):
    """Response shape for GET /enquiries, GET /enquiries/{id}."""

    id: str
    name: str
    email: str
    company: str
    status: str
    message: str
    received_at: datetime
    warning_missing_name: bool
    warning_invalid_email: bool
    warning_domain_mismatch: bool
    warning_possible_duplicate: bool
    duplicate_of: str | None
    claimed_by: str | None
    claim_expires_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    latest_analysis_id: str | None = None
    analysis_status: str | None = None
    analysis_result: dict[str, Any] | None = None
    safety_decision: str | None = None


class EnquiryListOut(BaseModel):
    """Response shape for GET /enquiries (§4.2: {items, next_cursor})."""

    items: list[EnquiryOut]
    next_cursor: str | None


def enquiry_to_out(e: Enquiry) -> EnquiryOut:
    """Map an Enquiry ORM instance to an EnquiryOut schema."""
    return EnquiryOut(
        id=e.id,
        name=e.name,
        email=e.email,
        company=e.company,
        status=e.status,
        message=e.message,
        received_at=e.received_at,
        warning_missing_name=e.warning_missing_name,
        warning_invalid_email=e.warning_invalid_email,
        warning_domain_mismatch=e.warning_domain_mismatch,
        warning_possible_duplicate=e.warning_possible_duplicate,
        duplicate_of=e.duplicate_of,
        claimed_by=str(e.claimed_by) if e.claimed_by else None,
        claim_expires_at=e.claim_expires_at,
        version=e.version,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


# Imported at bottom to avoid circular import.
from app.domain.models import Enquiry  # noqa: E402, F401
