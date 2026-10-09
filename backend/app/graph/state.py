"""Graph state types (§3.3).

TriageState is the LangGraph state for the enquiry_triage graph.
ScreenVerdicts holds parallel screener results.
ReviewPrompt/ReviewDecision implement the interrupt/resume contract (Fix 1).
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel

from app.domain.enums import Decision


class Verdict(BaseModel):
    """Screener verdict (§3.2)."""

    stage: str
    decision: Decision
    reason_codes: list[str] = []
    evidence: list[str] = []
    severity: int = 0


class ScreenVerdicts(TypedDict, total=False):
    """Parallel screener results — each screen writes only its own key."""

    deterministic: Verdict | None
    injection: Verdict | None
    content_safety: Verdict | None
    pii: Verdict | None


class AnalysisOut(BaseModel):
    """AI analysis result — the structured output the LLM produces."""

    summary: str = ""
    category: str = ""
    priority: str = ""
    reason: str = ""
    suggested_action: str = ""
    missing_info: list[str] = []
    risk_flags: list[str] = []
    needs_human_call: bool = False


class Usage(BaseModel):
    """LLM token/cost usage (§3.2)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""
    prompt_version: str = ""


class GraphError(BaseModel):
    """Error from a graph node."""

    node: str
    message: str


class ReviewPrompt(BaseModel):
    """Surfaced to the UI via interrupt() — pure function of state (Fix 1)."""

    analysis_id: str
    final_verdict: dict[str, Any]
    result: dict[str, Any] | None
    decision_set: list[str]
    requires_typed_reason: bool


class ReviewDecision(BaseModel):
    """Supplied via Command(resume=...) to resume the graph (Fix 1)."""

    action: str  # approve|edit_and_approve|reject|override_block
    reviewer_id: str
    expected_analysis_version: int
    edited_fields: dict[str, Any] | None = None
    typed_reason: str | None = None


class TriageState(TypedDict, total=False):
    """LangGraph state for enquiry_triage (§3.3)."""

    enquiry_id: str
    analysis_attempt: int
    raw: dict[str, Any]
    input_hash: str
    prompt_version: str
    schema_version: str
    screen_verdicts: ScreenVerdicts
    merged_verdict: dict[str, Any] | None
    masked_text: str | None
    llm_result: dict[str, Any] | None
    llm_usage: dict[str, Any] | None
    validated_result: dict[str, Any] | None
    final_verdict: dict[str, Any] | None
    review: dict[str, Any] | None
    node_versions: dict[str, str]
    error: dict[str, Any] | None
