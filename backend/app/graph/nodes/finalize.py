"""finalize node (§3.3 — finalize).

Applies the review decision to produce the final analysis status.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import TriageState


async def finalize(state: TriageState) -> dict[str, Any]:
    """Apply the review decision and set final status."""
    review: dict[str, Any] = state.get("review") or {}
    action = review.get("action", "reject")
    validated_result: dict[str, Any] = state.get("validated_result") or {}

    # If edit_and_approve, merge edited_fields into the result
    if action in {"edit_and_approve", "override_block"} and review.get("edited_fields"):
        validated_result = {**validated_result, **review["edited_fields"]}

    # The analysis status will be: approved | rejected
    status = (
        "approved" if action in ("approve", "edit_and_approve", "override_block") else "rejected"
    )

    return {
        "validated_result": validated_result,
        "review": {**review, "status": status},
    }
