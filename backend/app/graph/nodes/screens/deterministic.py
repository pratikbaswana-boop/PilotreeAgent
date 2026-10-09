"""Deterministic screener node (§3.3 — screen_deterministic).

Uses the DeterministicScreener to check heuristics.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import TriageState
from app.screeners.heuristics import DeterministicScreener
from app.screeners.protocol import ScreenContext

_screener = DeterministicScreener()


async def screen_deterministic(state: TriageState) -> dict[str, Any]:
    """Run deterministic heuristic checks."""
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
        "screen_verdicts": {"deterministic": verdict.model_dump()},
    }
