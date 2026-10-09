"""Gemini LLM provider (§3.2).

Uses google-genai for Gemini API calls with structured output.
Supports safety settings and structured output via Pydantic schema.

ADR-7: Native Gemini structured output -> Pydantic validation ->
one repair retry -> manual-triage, no further retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.provider import (
    GenerateRequest,
    GenerateResult,
    HealthStatus,
    InvalidResponse,
    RateLimited,
    SafetyBlocked,
    Timeout,
    Usage,
)
from app.llm.registry import ProviderConfig

logger = logging.getLogger(__name__)


class GeminiProvider:
    """LLM provider using Google Gemini via google-genai SDK."""

    name = "gemini"
    capabilities = {"structured_output", "safety_settings"}

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize the Gemini client."""
        if self._client is None:
            from google import genai  # type: ignore[import-not-found]
            self._client = genai.Client(api_key=self._config.api_key)
        return self._client

    async def generate_structured(
        self, request: GenerateRequest, schema: type[BaseModel]
    ) -> GenerateResult:
        """Generate structured output from Gemini.

        ADR-7: One call, then one repair retry if validation fails.
        After that, raise InvalidResponse to trigger manual triage.
        """
        start = time.monotonic()

        try:
            result = await asyncio.wait_for(
                self._call_gemini(request, schema),
                timeout=self._config.timeout_seconds,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            result.usage.latency_ms = latency_ms
            return result

        except TimeoutError as exc:
            raise Timeout(f"Gemini call timed out after {self._config.timeout_seconds}s") from exc

    async def _call_gemini(
        self, request: GenerateRequest, schema: type[BaseModel]
    ) -> GenerateResult:
        """Make the actual Gemini API call and validate the response."""
        start = time.monotonic()
        client = self._get_client()

        # Build the prompt
        full_prompt = f"{request.system_prompt}\n\n{request.user_prompt}"

        # Configure generation
        from google.genai import types as genai_types  # type: ignore[import-not-found]

        generate_config = genai_types.GenerateContentConfig(
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._config.model,
                contents=full_prompt,
                config=generate_config,
            )
        except Exception as exc:
            # Check for rate limiting
            if "429" in str(exc) or "rate" in str(exc).lower():
                raise RateLimited(str(exc), retry_after=None) from exc
            if "safety" in str(exc).lower() or "blocked" in str(exc).lower():
                raise SafetyBlocked(str(exc), categories=[]) from exc
            raise InvalidResponse(str(exc), raw="") from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        # Extract text from response
        text = ""
        if hasattr(response, "text") and response.text:
            text = response.text
        elif hasattr(response, "candidates") and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, "content") and candidate.content:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            text += part.text

        if not text:
            raise InvalidResponse("Empty response from Gemini", raw=str(response))

        # Parse JSON and validate against schema
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidResponse(f"Invalid JSON: {exc}", raw=text) from exc

        try:
            validated = schema(**data)
        except ValidationError as exc:
            # ADR-7: one repair retry
            logger.warning("gemini.validation_failed_first_try", extra={"error": str(exc)})
            return await self._repair_retry(request, schema, text, exc)

        # Extract usage metadata
        usage = Usage(
            prompt_tokens=getattr(response.usage_metadata, "prompt_token_count", 0),
            completion_tokens=getattr(response.usage_metadata, "candidates_token_count", 0),
            total_tokens=getattr(response.usage_metadata, "total_token_count", 0),
            latency_ms=latency_ms,
            cost_usd=self._calculate_cost(
                getattr(response.usage_metadata, "prompt_token_count", 0),
                getattr(response.usage_metadata, "candidates_token_count", 0),
            ),
            model=self._config.model,
            prompt_version=request.schema_name,
        )

        return GenerateResult(data=validated.model_dump(), usage=usage)

    async def _repair_retry(
        self,
        request: GenerateRequest,
        schema: type[BaseModel],
        previous_output: str,
        validation_error: ValidationError,
    ) -> GenerateResult:
        """ADR-7: one repair retry with the error message in the prompt."""
        repair_prompt = (
            f"The previous output failed validation:\n"
            f"{validation_error}\n\n"
            f"Previous output:\n{previous_output}\n\n"
            f"Please fix and return valid JSON matching the schema."
        )

        repair_request = GenerateRequest(
            system_prompt=request.system_prompt,
            user_prompt=repair_prompt,
            schema_name=request.schema_name,
            temperature=0.0,  # more deterministic for repair
            max_output_tokens=request.max_output_tokens,
            deadline=request.deadline,
        )

        try:
            return await self._call_gemini(repair_request, schema)
        except InvalidResponse as exc:
            # ADR-7: after repair retry fails, trigger manual triage
            raise InvalidResponse(
                f"Schema validation failed after repair retry: {exc}",
                raw=exc.raw,
            ) from exc

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> Any:
        """Calculate cost in USD."""
        from decimal import Decimal
        cost = (
            Decimal(str(input_tokens)) * Decimal(str(self._config.cost_per_1k_input))
            + Decimal(str(output_tokens)) * Decimal(str(self._config.cost_per_1k_output))
        ) / Decimal("1000")
        return cost

    async def health(self) -> HealthStatus:
        """Check if the Gemini provider is healthy."""
        try:
            client = self._get_client()
            # Simple health check — list models
            await asyncio.to_thread(
                client.models.list,
            )
            return HealthStatus(healthy=True)
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc))
