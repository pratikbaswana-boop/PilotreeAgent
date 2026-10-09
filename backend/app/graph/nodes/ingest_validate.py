"""ingest_validate node — validates enquiry input (§3.3).

M4 stub: validates that the enquiry exists and has required fields.
Terminal/validation errors short-circuit to await_review with error set.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import Decision
from app.graph.state import TriageState, Verdict


async def ingest_validate(state: TriageState) -> dict[str, Any]:
    """Validate the enquiry input. Sets error on invalid input."""
    raw = state.get("raw", {})
    enquiry_id = state.get("enquiry_id", "")

    if not enquiry_id or not raw.get("message"):
        return {
            "error": {"node": "ingest_validate", "message": "Missing enquiry_id or message"},
            "final_verdict": Verdict(
                stage="ingest",
                decision=Decision.BLOCK,
                reason_codes=["ingest_invalid"],
                evidence=["Missing required fields"],
                severity=100,
            ).model_dump(),
        }

    return {}
