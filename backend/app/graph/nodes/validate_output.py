"""validate_output node (§3.3 — validate_output).

Validates the LLM result against the Zod-equivalent Pydantic schema.
On failure, sets error and short-circuits to await_review.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.graph.state import AnalysisOut, TriageState


async def validate_output(state: TriageState) -> dict[str, Any]:
    """Validate LLM output against the AnalysisOut schema."""
    llm_result = state.get("llm_result", {})
    if not llm_result:
        return {
            "error": {"node": "validate_output", "message": "No LLM result to validate"},
            "validated_result": None,
        }

    try:
        validated = AnalysisOut(**llm_result)
        return {"validated_result": validated.model_dump(), "error": None}
    except ValidationError as exc:
        return {
            "error": {
                "node": "validate_output",
                "message": f"Schema validation failed: {exc}",
            },
            "validated_result": None,
        }
