"""LLM provider protocol and errors (§3.2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel

from app.screeners.protocol import DeadlineBudget


class ProviderError(Exception):
    """Base error for LLM provider failures."""


class RateLimited(ProviderError):
    """Provider returned 429."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Timeout(ProviderError):
    """Provider call timed out."""


class ProviderUnavailable(ProviderError):
    """All providers in the chain failed."""


class InvalidResponse(ProviderError):
    """Provider returned unparseable or invalid output."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class SafetyBlocked(ProviderError):
    """Provider's safety filters blocked the request."""

    def __init__(self, message: str, categories: list[str] | None = None) -> None:
        super().__init__(message)
        self.categories = categories or []


class Usage(BaseModel):
    """LLM token/cost usage (§3.2)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    model: str = ""
    prompt_version: str = ""


class GenerateRequest(BaseModel):
    """Request to an LLM provider."""

    system_prompt: str
    user_prompt: str
    schema_name: str
    temperature: float = 0.1
    max_output_tokens: int = 1024
    deadline: DeadlineBudget = DeadlineBudget()


class GenerateResult(BaseModel):
    """Result from an LLM provider."""

    data: dict[str, Any]  # validated by caller against target schema
    usage: Usage


class HealthStatus(BaseModel):
    """Health status of a provider."""

    healthy: bool
    detail: str | None = None


class LLMProvider(Protocol):
    """Protocol that all LLM providers implement."""

    name: str
    capabilities: set[str]

    async def generate_structured(
        self, request: GenerateRequest, schema: type[BaseModel]
    ) -> GenerateResult: ...

    async def health(self) -> HealthStatus: ...
