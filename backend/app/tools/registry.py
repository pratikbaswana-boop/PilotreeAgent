"""Tool registry (§3.2, ADR-11).

Maintains a registry of tool implementations. Adding a tool is one class +
one config row, no graph changes.
"""

from __future__ import annotations

from app.tools.protocol import Tool


class ToolRegistry:
    """Registry of tool implementations by key."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def get(self, key: str) -> Tool:
        """Get a tool by key."""
        tool = self._tools.get(key)
        if tool is None:
            raise KeyError(f"Tool '{key}' not registered")
        return tool

    def all(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def keys(self) -> list[str]:
        """Return all registered tool keys."""
        return list(self._tools.keys())

    def has(self, key: str) -> bool:
        """Check if a tool is registered."""
        return key in self._tools
