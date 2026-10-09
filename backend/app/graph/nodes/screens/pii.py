"""PII masking node (§3.3 — screen_pii).

Uses the PIIScreener (Presidio) for PII detection and masking.
Returns both the PII verdict and the masked text in state.
Presidio runs in a thread pool (ADR-8).
"""

from __future__ import annotations

from typing import Any

from app.graph.state import TriageState
from app.screeners.pii_presidio import PIIScreener
from app.screeners.protocol import ScreenContext

_screener = PIIScreener()


async def screen_pii(state: TriageState) -> dict[str, Any]:
    """Run PII detection and masking."""
    raw = state.get("raw", {})
    message = raw.get("message", "")
    metadata = raw.get("metadata", {})

    ctx = ScreenContext(
        enquiry_id=state.get("enquiry_id", ""),
        text=message,
        metadata=metadata,
    )

    verdict = await _screener.scan(ctx)

    # Mask the text regardless of verdict — if PII is detected,
    # the masked text replaces the original for the LLM call.
    masked_text = await _screener.mask(message)

    return {
        "masked_text": masked_text,
        "screen_verdicts": {"pii": verdict.model_dump()},
    }
