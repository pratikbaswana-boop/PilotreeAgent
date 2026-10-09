"""Screener protocol (§3.2).

Defines the Protocol that all screeners implement, plus the ScreenContext
that carries enquiry data and deadline budget to each screener.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.domain.enums import Decision


class DeadlineBudget(BaseModel):
    """Deadline budget for a screener pass."""

    total_ms: int = 5000
    remaining_ms: int = 5000


class ScreenContext(BaseModel):
    """Input context for a screener scan."""

    enquiry_id: str
    text: str
    status: str = "new"
    metadata: dict[str, str] = {}
    deadline: DeadlineBudget = DeadlineBudget()


class Verdict(BaseModel):
    """Screener verdict (§3.2)."""

    stage: str
    decision: Decision
    reason_codes: list[str] = []
    evidence: list[str] = []
    severity: int = 0


class Screener(Protocol):
    """Protocol that all screeners implement."""

    name: str

    async def scan(self, ctx: ScreenContext) -> Verdict: ...
