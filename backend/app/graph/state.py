"""Graph state types (§3.3).

TriageState is the LangGraph state for the enquiry_triage graph.
ScreenVerdicts holds parallel screener results.
ReviewPrompt/ReviewDecision implement the interrupt/resume contract (Fix 1).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

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


class RecommendedTool(BaseModel):
    tool: str
    reason: str = Field(max_length=200)
    is_primary: bool


class AnalysisOut(BaseModel):
    """AI analysis result — the structured output the LLM produces."""

    summary: str = Field(max_length=300)
    category: Literal[
        "delivery_issue",
        "failed_delivery",
        "damage_claim",
        "billing_query",
        "booking_or_quote",
        "sales_lead",
        "reporting_request",
        "suspicious",
        "other",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    priority_reason: str = Field(max_length=200)
    suggested_action: str = Field(max_length=300)
    missing_info: list[str] = Field(default_factory=list, max_length=6)
    risk_flags: list[
        Literal[
            "time_critical",
            "cold_chain",
            "possible_fraud",
            "injection_suspected",
            "repeat_contact",
            "status_conflict",
            "our_fault_possible",
        ]
    ] = Field(default_factory=list)
    needs_human_call: bool = False
    recommended_tools: list[RecommendedTool] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_unique_lists_and_primary(self) -> AnalysisOut:
        if len(self.missing_info) != len(set(self.missing_info)):
            raise ValueError("missing_info must not contain duplicates")
        if len(self.risk_flags) != len(set(self.risk_flags)):
            raise ValueError("risk_flags must not contain duplicates")
        names = [item.tool for item in self.recommended_tools]
        if len(names) != len(set(names)):
            raise ValueError("recommended_tools must not contain duplicates")
        primary_count = sum(item.is_primary for item in self.recommended_tools)
        if primary_count != (1 if self.recommended_tools else 0):
            raise ValueError("recommended_tools must contain exactly one primary tool")
        return self


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
