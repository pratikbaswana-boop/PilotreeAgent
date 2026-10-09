"""Injection screener node (§3.3 — screen_injection).

Uses the InjectionScreener for heuristic injection detection.
M6 will add a Gemini judge behind the same interface.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import TriageState
from app.screeners.injection_judge import InjectionScreener
from app.screeners.protocol import ScreenContext

_screener = InjectionScreener()


async def screen_injection(state: TriageState) -> dict[str, Any]:
    """Run injection detection."""
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
        "screen_verdicts": {"injection": verdict.model_dump()},
    }
