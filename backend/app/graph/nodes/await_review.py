"""await_review node (§3.3 — await_review).

This is the interrupt point where the graph pauses for human review.
The node calls interrupt() with a ReviewPrompt, and resumes when the
reviewer supplies a Command(resume=ReviewDecision).

Fix 1: ReviewPrompt is a pure function of state — not stored in the DB.
Fix 1: All review actions (confirm/edit/quarantine_confirm/reject/override_block)
go through resume-review, not POST /reviews.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.domain.enums import Decision
from app.graph.state import ReviewPrompt, TriageState


def _build_review_prompt(state: TriageState) -> ReviewPrompt:
    """Build the review prompt from current state (pure function, Fix 1)."""
    final_verdict: dict[str, Any] = state.get("final_verdict") or {}
    validated_result = state.get("validated_result")
    error = state.get("error")

    # Determine decision_set based on verdict and error
    decision = (
        Decision(final_verdict.get("decision", Decision.ALLOW.value))
        if final_verdict
        else Decision.ALLOW
    )
    has_error = error is not None

    if has_error:
        decision_set = ["reject"]
        requires_typed_reason = True
    elif decision == Decision.BLOCK:
        # BLOCK: only admin can override (Fix 10)
        decision_set = ["reject", "override_block"]
        requires_typed_reason = True
    elif decision == Decision.QUARANTINE:
        decision_set = ["approve", "edit_and_approve", "reject"]
        requires_typed_reason = True
    else:
        decision_set = ["approve", "edit_and_approve", "reject"]
        requires_typed_reason = False

    return ReviewPrompt(
        analysis_id=state.get("enquiry_id", ""),
        final_verdict=final_verdict,
        result=validated_result,
        decision_set=decision_set,
        requires_typed_reason=requires_typed_reason,
    )


async def await_review(state: TriageState) -> dict[str, Any]:
    """Interrupt for human review. Resumes via Command(resume=ReviewDecision)."""
    prompt = _build_review_prompt(state)
    # interrupt() suspends execution and returns the value to the caller.
    # When resumed via Command(resume=decision), the value is returned here.
    decision_data: dict[str, Any] = interrupt(prompt.model_dump())
    return {"review": decision_data}
