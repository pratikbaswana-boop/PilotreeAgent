"""Provider registry (§3.2).

Maintains an ordered list of LLM providers for the ProviderRouter to try.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.provider import LLMProvider


@dataclass
class ProviderConfig:
    """Configuration for a registered provider."""

    model: str
    api_key: str
    temperature: float = 0.1
    max_output_tokens: int = 1024
    timeout_seconds: int = 30
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


@dataclass
class ProviderEntry:
    """A registered provider with its priority and config."""

    provider: LLMProvider
    priority: int
    config: ProviderConfig


class ProviderRegistry:
    """Registry of LLM providers, ordered by priority (lower = higher priority)."""

    def __init__(self) -> None:
        self._entries: dict[str, ProviderEntry] = {}

    def register(
        self,
        provider: LLMProvider,
        *,
        priority: int,
        config: ProviderConfig,
    ) -> None:
        """Register a provider with a priority and config."""
        self._entries[provider.name] = ProviderEntry(
            provider=provider, priority=priority, config=config
        )

    def get(self, name: str) -> LLMProvider:
        """Get a provider by name."""
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Provider '{name}' not registered")
        return entry.provider

    def ordered(self) -> list[ProviderEntry]:
        """Return providers sorted by priority (1 = highest)."""
        return sorted(self._entries.values(), key=lambda e: e.priority)

    def names(self) -> list[str]:
        """Return all registered provider names."""
        return list(self._entries.keys())

    def get_config(self, name: str) -> ProviderConfig:
        """Get the config for a provider."""
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Provider '{name}' not registered")
        return entry.config
