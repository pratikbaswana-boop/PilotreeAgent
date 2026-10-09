"""Content safety screener node (§3.3 — screen_content_safety).

Uses the ContentSafetyScreener for heuristic content safety checks.
M6 will add a Gemini safety judge behind the same interface.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import TriageState
from app.screeners.protocol import ScreenContext
from app.screeners.safety_judge import ContentSafetyScreener

_screener = ContentSafetyScreener()


async def screen_content_safety(state: TriageState) -> dict[str, Any]:
    """Run content safety checks."""
    raw = state.get("raw", {})
    message = raw.get("message", "")
    metadata = raw.get("metadata", {})

    ctx = ScreenContext(
        enquiry_id=state.get("enquiry_id", ""),
        text=message,
        metadata=metadata,
    )

    verdict = await _screener.scan(ctx)
    return {
        "screen_verdicts": {"content_safety": verdict.model_dump()},
    }
