"""Slack tool integration (§3.2).

Posts a message to a Slack channel via webhook.
Slack webhooks have no query API, so reconcile() always returns None (Fix 8).
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from app.tools.protocol import ToolHealthStatus, ToolResult, ToolUIMetadata


class SlackConfig(BaseModel):
    """Configuration for the Slack tool."""

    webhook_url: str
    channel: str = "#triage"


class SlackTool:
    """Slack webhook tool — posts messages to a channel."""

    name = "slack"
    config_schema = SlackConfig
    required_scopes: set[str] = set()
    ui_metadata = ToolUIMetadata(
        key="slack",
        label="Slack",
        icon="slack",
        suggested_for=["high", "urgent"],
        payload_preview_fields=["summary", "category", "priority"],
        enabled=True,
    )

    def __init__(self, config: SlackConfig) -> None:
        self._config = config

    async def validate(self, payload: dict[str, Any]) -> None:
        """Validate the payload before sending."""
        if not payload.get("summary"):
            raise ValueError("Payload must contain 'summary'")

    async def execute(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult:
        """Post a message to Slack via webhook."""
        try:
            message = {
                "channel": self._config.channel,
                "text": payload.get("summary", "Triage notification"),
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{payload.get('category', 'N/A')}*"
                                f" — {payload.get('priority', 'N/A')}\n"
                                f"{payload.get('summary', '')}\n"
                                f"_{payload.get('suggested_action', '')}_"
                            ),
                        },
                    }
                ],
                "metadata": {"event_payload": {"idempotency_key": idempotency_key}},
            }

            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=2)) as client:
                response = await client.post(
                    self._config.webhook_url,
                    json=message,
                )

            if response.status_code == 200:
                return ToolResult(status="sent", external_id=None)
            elif response.status_code == 429:
                return ToolResult(
                    status="failed",
                    error="Rate limited",
                    retryable=True,
                    retry_after_seconds=float(response.headers.get("Retry-After", "1")),
                )
            elif 400 <= response.status_code < 500:
                return ToolResult(
                    status="failed",
                    error=f"Client error: {response.status_code}",
                    retryable=False,
                )
            else:
                return ToolResult(
                    status="failed",
                    error=f"Server error: {response.status_code}",
                    retryable=True,
                )

        except (httpx.ConnectTimeout, httpx.PoolTimeout):
            return ToolResult(status="failed", error="Connection timed out", retryable=True)
        except httpx.TimeoutException:
            # Slack has no reconcile capability — timeout = unknown (Fix 8)
            return ToolResult(
                status="unknown",
                error="Request timed out — Slack has no query API to reconcile",
                retryable=False,
            )
        except httpx.ConnectError:
            return ToolResult(
                status="failed",
                error="Connection failed",
                retryable=True,
            )
        except Exception as exc:
            return ToolResult(
                status="failed",
                error=str(exc),
                retryable=False,
            )

    async def health(self) -> ToolHealthStatus:
        """Check if the Slack webhook is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Slack webhooks respond to GET with 200
                response = await client.get(self._config.webhook_url)
            if response.status_code < 500:
                return ToolHealthStatus(healthy=True, breaker_state="closed", detail=None)
            return ToolHealthStatus(
                healthy=False,
                breaker_state="closed",
                detail=f"HTTP {response.status_code}",
            )
        except Exception as exc:
            return ToolHealthStatus(healthy=False, breaker_state="closed", detail=str(exc))

    def map_from_analysis(self, enquiry: Any, analysis: Any) -> dict[str, Any]:
        """Map enquiry + analysis to a Slack payload."""
        return {
            "summary": getattr(analysis, "summary", ""),
            "category": getattr(analysis, "category", ""),
            "priority": getattr(analysis, "priority", ""),
            "suggested_action": getattr(analysis, "suggested_action", ""),
            "enquiry_id": getattr(enquiry, "id", ""),
            "customer_name": getattr(enquiry, "name", ""),
            "company": getattr(enquiry, "company", ""),
        }

    async def reconcile(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult | None:
        """Slack webhooks have no query API — reconcile always returns None (Fix 8)."""
        return None
