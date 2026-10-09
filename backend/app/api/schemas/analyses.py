"""Analysis API schemas (§4.2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AnalysisRequest(BaseModel):
    """Request body for POST /enquiries/{id}/analyses."""

    force: bool = False


class AnalysisResponse(BaseModel):
    """Response for POST /enquiries/{id}/analyses (202)."""

    job_id: int | None
    analysis_id: str
    attached: bool
    reused: bool


class AnalysisStatus(BaseModel):
    """Response for GET /analyses/{id} — AnalysisStatus."""

    id: str
    enquiry_id: str
    analysis_attempt: int
    status: str
    result: dict[str, Any] | None = None
    llm_usage: dict[str, Any] | None = None
    model: str | None = None
    version: int
    review_prompt: dict[str, Any] | None = None
    safety_verdicts: list[dict[str, Any]] = []
    job_id: int | None = None
    phase: str = "queued"
    node: str = "queued"


class JobStatusResponse(BaseModel):
    """Response for GET /jobs/{id} (Fix 6)."""

    id: int
    status: str
    kind: str
    attempts: int
    last_error: str | None = None


class ReviewDecisionRequest(BaseModel):
    """Request body for POST /analyses/{id}/resume-review."""

    action: str  # approve|edit_and_approve|reject|override_block
    reviewer_id: str
    expected_analysis_version: int
    edited_fields: dict[str, Any] | None = None
    typed_reason: str | None = None


class AnalysisPatchRequest(BaseModel):
    """Request body for PATCH /analyses/{id} — partial AnalysisOut edits."""

    summary: str | None = None
    category: str | None = None
    priority: str | None = None
    reason: str | None = None
    suggested_action: str | None = None
    missing_info: list[str] | None = None
    risk_flags: list[str] | None = None
    needs_human_call: bool | None = None
