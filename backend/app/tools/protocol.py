"""Tool protocol and types (§3.2).

Every tool integration (Slack, Linear, Sheets, ...) implements this protocol.
The outbox worker calls Tool.execute() after preconditions are met.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ToolHealthStatus(BaseModel):
    """Health status of a tool integration."""

    healthy: bool
    breaker_state: str  # closed | open | half_open
    detail: str | None = None


class ToolResult(BaseModel):
    """Result of a tool execute call (Fix 8)."""

    status: str  # sent | failed | unknown
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None


class ToolUIMetadata(BaseModel):
    """UI metadata for a tool (drives destination chips)."""

    key: str
    label: str
    icon: str
    suggested_for: list[str]  # Priority values
    payload_preview_fields: list[str]
    enabled: bool


class Tool(Protocol):
    """Protocol that all tool integrations implement."""

    name: str
    config_schema: type[BaseModel]
    required_scopes: set[str]
    ui_metadata: ToolUIMetadata

    async def validate(self, payload: dict[str, Any]) -> None: ...

    async def execute(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult: ...

    async def health(self) -> ToolHealthStatus: ...

    def map_from_analysis(self, enquiry: Any, analysis: Any) -> dict[str, Any]: ...

    async def reconcile(
        self, payload: dict[str, Any], idempotency_key: str
    ) -> ToolResult | None: ...
