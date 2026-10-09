"""Stub LLM provider for testing (§3.2).

Returns deterministic canned responses for testing the graph and router
without calling a real LLM API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.provider import (
    GenerateRequest,
    GenerateResult,
    HealthStatus,
    InvalidResponse,
    ProviderError,
    Usage,
)


class StubProvider:
    """Deterministic stub LLM provider for testing.

    Can be configured to return a specific response or raise a specific
    error on the Nth call.
    """

    name = "stub"
    capabilities = {"structured_output", "safety_settings"}

    def __init__(
        self,
        response_data: dict[str, Any] | None = None,
        error: ProviderError | None = None,
        error_on_call: int = 0,
    ) -> None:
        self._response_data = response_data or {
            "summary": "Stub analysis result.",
            "category": "other",
            "priority": "medium",
            "priority_reason": "Stub provider test.",
            "recommended_tools": [],
            "suggested_action": "Review manually.",
            "missing_info": [],
            "risk_flags": [],
            "needs_human_call": False,
        }
        self._error = error
        self._error_on_call = error_on_call
        self._call_count = 0

    async def generate_structured(
        self, request: GenerateRequest, schema: type[BaseModel]
    ) -> GenerateResult:
        self._call_count += 1

        if self._error and self._call_count >= self._error_on_call:
            raise self._error

        # Validate against schema
        try:
            validated = schema(**self._response_data)
        except Exception as exc:
            raise InvalidResponse(str(exc), raw=str(self._response_data)) from exc

        return GenerateResult(
            data=validated.model_dump(),
            usage=Usage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                latency_ms=10,
                model="stub",
                prompt_version=request.schema_name,
            ),
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(healthy=True)
