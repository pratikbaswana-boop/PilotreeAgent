"""Provider router (§3.2).

Tries providers in priority order, skipping open-breaker/unhealthy ones,
applying per-provider bulkhead + rate limit. Raises ProviderUnavailable
if the whole chain fails.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import (
    GenerateRequest,
    GenerateResult,
    HealthStatus,
    InvalidResponse,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SafetyBlocked,
    Timeout,
)
from app.llm.registry import ProviderRegistry
from app.resilience.breaker import CircuitBreaker
from app.resilience.bulkhead import BulkheadRegistry
from app.resilience.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


class ProviderRouter:
    """Routes LLM calls through providers in priority order with resilience."""

    def __init__(
        self,
        registry: ProviderRegistry,
        breaker: CircuitBreaker,
        bulkheads: BulkheadRegistry,
        limiter: RateLimiter,
    ) -> None:
        self._registry = registry
        self._breaker = breaker
        self._bulkheads = bulkheads
        self._limiter = limiter

    async def generate_structured(
        self,
        db: AsyncSession,
        request: GenerateRequest,
        schema: type[BaseModel],
    ) -> GenerateResult:
        """Try providers in priority order with breaker/bulkhead/rate-limit."""
        errors: list[str] = []

        for entry in self._registry.ordered():
            provider = entry.provider
            breaker_key = f"provider:{provider.name}"

            # Check breaker
            if not await self._breaker.allow(db, breaker_key):
                logger.info(
                    "provider.skipped_breaker_open",
                    extra={"provider": provider.name},
                )
                errors.append(f"{provider.name}: breaker open")
                continue

            # Check rate limit
            rate_decision = await self._limiter.acquire(db, "provider", provider.name, cost=1)
            if not rate_decision.allowed:
                logger.info(
                    "provider.skipped_rate_limited",
                    extra={"provider": provider.name},
                )
                errors.append(f"{provider.name}: rate limited")
                continue

            # Acquire bulkhead
            await self._bulkheads.acquire(f"provider:{provider.name}", max_concurrent=10)

            try:
                result = await provider.generate_structured(request, schema)
                # Success — close the breaker
                await self._breaker.record_success(db, breaker_key)
                return result
            except RateLimited as exc:
                await self._breaker.record_failure(db, breaker_key)
                errors.append(f"{provider.name}: rate limited ({exc})")
                logger.warning("provider.rate_limited", extra={"provider": provider.name})
            except Timeout as exc:
                await self._breaker.record_failure(db, breaker_key)
                errors.append(f"{provider.name}: timeout ({exc})")
                logger.warning("provider.timeout", extra={"provider": provider.name})
            except SafetyBlocked as exc:
                # Safety blocks are not breaker-worthy — it's a content issue, not a provider issue
                errors.append(f"{provider.name}: safety blocked ({exc})")
                logger.warning("provider.safety_blocked", extra={"provider": provider.name})
                raise
            except InvalidResponse as exc:
                await self._breaker.record_failure(db, breaker_key)
                errors.append(f"{provider.name}: invalid response ({exc})")
                logger.warning("provider.invalid_response", extra={"provider": provider.name})
            except ProviderError as exc:
                await self._breaker.record_failure(db, breaker_key)
                errors.append(f"{provider.name}: error ({exc})")
                logger.warning("provider.error", extra={"provider": provider.name})
            except Exception as exc:
                await self._breaker.record_failure(db, breaker_key)
                errors.append(f"{provider.name}: unexpected error ({exc})")
                logger.exception("provider.unexpected_error", extra={"provider": provider.name})
            finally:
                self._bulkheads.release(f"provider:{provider.name}")

        raise ProviderUnavailable(f"All providers failed: {'; '.join(errors)}")

    async def health(self) -> dict[str, HealthStatus]:
        """Check health of all providers."""
        results: dict[str, HealthStatus] = {}
        for entry in self._registry.ordered():
            try:
                results[entry.provider.name] = await entry.provider.health()
            except Exception as exc:
                results[entry.provider.name] = HealthStatus(healthy=False, detail=str(exc))
        return results
