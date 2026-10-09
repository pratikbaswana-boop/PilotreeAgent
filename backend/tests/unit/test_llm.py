"""Tests for the LLM provider layer and resilience (§3.2, §3.6, §3.8, M6)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.llm.provider import (
    GenerateRequest,
    ProviderUnavailable,
    Timeout,
)
from app.llm.registry import ProviderConfig, ProviderRegistry
from app.llm.router import ProviderRouter
from app.llm.stub import StubProvider
from app.resilience.breaker import CircuitBreaker
from app.resilience.bulkhead import BulkheadRegistry
from app.resilience.rate_limit import RateLimiter
from app.screeners.protocol import DeadlineBudget

# ── Stub Provider ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_provider_returns_valid_result() -> None:
    """Stub provider returns a valid structured result."""
    from app.graph.state import AnalysisOut

    provider = StubProvider(
        response_data={
            "summary": "Test summary",
            "category": "delivery_issue",
            "priority": "high",
            "priority_reason": "Late delivery",
            "suggested_action": "Contact carrier",
            "missing_info": [],
            "risk_flags": [],
            "needs_human_call": False,
        }
    )

    request = GenerateRequest(
        system_prompt="You are a logistics triage assistant.",
        user_prompt="My delivery is late.",
        schema_name="AnalysisOut",
        deadline=DeadlineBudget(),
    )

    result = await provider.generate_structured(request, AnalysisOut)
    assert result.data["summary"] == "Test summary"
    assert result.data["category"] == "delivery_issue"
    assert result.usage.total_tokens == 150
    assert result.usage.model == "stub"


@pytest.mark.asyncio
async def test_stub_provider_raises_error() -> None:
    """Stub provider raises the configured error on the Nth call."""
    from app.graph.state import AnalysisOut

    provider = StubProvider(
        error=Timeout("Simulated timeout"),
        error_on_call=1,
    )

    request = GenerateRequest(
        system_prompt="test",
        user_prompt="test",
        schema_name="AnalysisOut",
        deadline=DeadlineBudget(),
    )

    with pytest.raises(Timeout):
        await provider.generate_structured(request, AnalysisOut)


# ── Provider Registry ─────────────────────────────────────────────


def test_provider_registry_ordered_by_priority() -> None:
    """Registry returns providers sorted by priority."""
    registry = ProviderRegistry()
    p1 = StubProvider()
    p2 = StubProvider()
    p1.name = "primary"
    p2.name = "fallback"

    registry.register(p1, priority=1, config=ProviderConfig(model="test", api_key="k"))
    registry.register(p2, priority=2, config=ProviderConfig(model="test", api_key="k"))

    ordered = registry.ordered()
    assert ordered[0].provider.name == "primary"
    assert ordered[1].provider.name == "fallback"


def test_provider_registry_get_by_name() -> None:
    """Registry can get a provider by name."""
    registry = ProviderRegistry()
    provider = StubProvider()
    provider.name = "test-get"
    registry.register(provider, priority=1, config=ProviderConfig(model="m", api_key="k"))

    assert registry.get("test-get") is provider
    with pytest.raises(KeyError):
        registry.get("nonexistent")


# ── Provider Router ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_uses_first_provider(db_engine: AsyncEngine) -> None:
    """Router uses the first available provider."""
    from app.graph.state import AnalysisOut

    registry = ProviderRegistry()
    provider = StubProvider(
        response_data={
            "summary": "Router test",
            "category": "other",
            "priority": "low",
            "priority_reason": "test",
            "suggested_action": "none",
            "missing_info": [],
            "risk_flags": [],
            "needs_human_call": False,
        }
    )
    registry.register(provider, priority=1, config=ProviderConfig(model="stub", api_key="k"))

    breaker = CircuitBreaker(failure_threshold=5)
    bulkheads = BulkheadRegistry()
    limiter = RateLimiter(capacity=100, refill_rate=10)
    router = ProviderRouter(registry, breaker, bulkheads, limiter)

    request = GenerateRequest(
        system_prompt="test",
        user_prompt="test",
        schema_name="AnalysisOut",
        deadline=DeadlineBudget(),
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await router.generate_structured(session, request, AnalysisOut)
        await session.commit()

    assert result.data["summary"] == "Router test"


@pytest.mark.asyncio
async def test_router_fails_over_to_second_provider(db_engine: AsyncEngine) -> None:
    """Router fails over to the second provider when the first fails."""
    from app.graph.state import AnalysisOut

    registry = ProviderRegistry()
    failing = StubProvider(error=Timeout("First provider timeout"), error_on_call=1)
    failing.name = "failing"
    succeeding = StubProvider(
        response_data={
            "summary": "Failover success",
            "category": "other",
            "priority": "low",
            "priority_reason": "test",
            "suggested_action": "none",
            "missing_info": [],
            "risk_flags": [],
            "needs_human_call": False,
        }
    )
    succeeding.name = "succeeding"

    registry.register(failing, priority=1, config=ProviderConfig(model="f", api_key="k"))
    registry.register(succeeding, priority=2, config=ProviderConfig(model="s", api_key="k"))

    breaker = CircuitBreaker(failure_threshold=100)  # high threshold so we don't open
    bulkheads = BulkheadRegistry()
    limiter = RateLimiter(capacity=100, refill_rate=10)
    router = ProviderRouter(registry, breaker, bulkheads, limiter)

    request = GenerateRequest(
        system_prompt="test",
        user_prompt="test",
        schema_name="AnalysisOut",
        deadline=DeadlineBudget(),
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        result = await router.generate_structured(session, request, AnalysisOut)
        await session.commit()

    assert result.data["summary"] == "Failover success"


@pytest.mark.asyncio
async def test_router_raises_unavailable_when_all_fail(db_engine: AsyncEngine) -> None:
    """Router raises ProviderUnavailable when all providers fail."""
    from app.graph.state import AnalysisOut

    registry = ProviderRegistry()
    failing = StubProvider(error=Timeout("Timeout"), error_on_call=1)
    failing.name = "failing"
    registry.register(failing, priority=1, config=ProviderConfig(model="f", api_key="k"))

    breaker = CircuitBreaker(failure_threshold=100)
    bulkheads = BulkheadRegistry()
    limiter = RateLimiter(capacity=100, refill_rate=10)
    router = ProviderRouter(registry, breaker, bulkheads, limiter)

    request = GenerateRequest(
        system_prompt="test",
        user_prompt="test",
        schema_name="AnalysisOut",
        deadline=DeadlineBudget(),
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        with pytest.raises(ProviderUnavailable):
            await router.generate_structured(session, request, AnalysisOut)
            await session.commit()


# ── Circuit Breaker ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold(db_engine: AsyncEngine) -> None:
    """Breaker opens after reaching the failure threshold."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        for _ in range(3):
            await breaker.record_failure(session, "test:breaker")

        snapshot = await breaker.state(session, "test:breaker")
        assert snapshot.state == "open"

        allowed = await breaker.allow(session, "test:breaker")
        assert allowed is False


@pytest.mark.asyncio
async def test_breaker_closes_on_success(db_engine: AsyncEngine) -> None:
    """Breaker closes on success after being open."""
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        # Open the breaker
        await breaker.record_failure(session, "test:close")
        await breaker.record_failure(session, "test:close")
        snapshot = await breaker.state(session, "test:close")
        assert snapshot.state == "open"

        # Record success — should close
        await breaker.record_success(session, "test:close")
        snapshot = await breaker.state(session, "test:close")
        assert snapshot.state == "closed"


@pytest.mark.asyncio
async def test_breaker_allows_when_closed(db_engine: AsyncEngine) -> None:
    """Breaker allows requests when closed."""
    breaker = CircuitBreaker(failure_threshold=5)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        allowed = await breaker.allow(session, "test:allow")
        assert allowed is True


# ── Rate Limiter ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_capacity(db_engine: AsyncEngine) -> None:
    """Rate limiter allows requests under capacity."""
    limiter = RateLimiter(capacity=10, refill_rate=1.0)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        decision = await limiter.acquire(session, "test", "rl1", cost=1)
        await session.commit()
        assert decision.allowed is True


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_capacity(db_engine: AsyncEngine) -> None:
    """Rate limiter blocks requests over capacity."""
    limiter = RateLimiter(capacity=2, refill_rate=0.001)  # very slow refill

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        # Use up all tokens
        d1 = await limiter.acquire(session, "test", "rl2", cost=1)
        assert d1.allowed is True
        d2 = await limiter.acquire(session, "test", "rl2", cost=1)
        assert d2.allowed is True
        # Third request should be blocked (capacity=2)
        d3 = await limiter.acquire(session, "test", "rl2", cost=1)
        assert d3.allowed is False
        await session.commit()


# ── Bulkhead ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulkhead_acquire_release() -> None:
    """Bulkhead semaphore can be acquired and released."""
    bulkhead = BulkheadRegistry()
    await bulkhead.acquire("test", max_concurrent=2)
    bulkhead.release("test")
    # Should be able to acquire again
    await bulkhead.acquire("test", max_concurrent=2)
    bulkhead.release("test")
