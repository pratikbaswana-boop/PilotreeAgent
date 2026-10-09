"""Linear tool integration (§3.2).

Creates a Linear issue via GraphQL API.
Linear supports a reliable lookup by our idempotency-key marker field,
so reconcile() can resolve ambiguous outcomes (Fix 8).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.tools.protocol import ToolHealthStatus, ToolResult, ToolUIMetadata

logger = logging.getLogger(__name__)


class LinearConfig(BaseModel):
    """Configuration for the Linear tool."""

    api_key: str
    team_id: str
    api_url: str = "https://api.linear.app/graphql"


class LinearTool:
    """Linear GraphQL tool — creates issues."""

    name = "linear"
    config_schema = LinearConfig
    required_scopes: set[str] = {"write"}
    ui_metadata = ToolUIMetadata(
        key="linear",
        label="Linear",
        icon="linear",
        suggested_for=["high", "medium"],
        payload_preview_fields=["summary", "category", "priority"],
        enabled=True,
    )

    def __init__(self, config: LinearConfig) -> None:
        self._config = config

    async def validate(self, payload: dict[str, Any]) -> None:
        """Validate the payload before sending."""
        if not payload.get("summary"):
            raise ValueError("Payload must contain 'summary'")

    async def execute(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult:
        """Create a Linear issue via GraphQL."""
        mutation = """
        mutation CreateIssue($title: String!, $description: String!, $teamId: String!) {
            issueCreate(input: {title: $title, description: $description, teamId: $teamId}) {
                success
                issue { id identifier }
            }
        }
        """

        variables = {
            "title": payload.get("summary", "Triage issue"),
            "description": self._build_description(payload, idempotency_key),
            "teamId": self._config.team_id,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=2)) as client:
                response = await client.post(
                    self._config.api_url,
                    json={"query": mutation, "variables": variables},
                    headers={
                        "Authorization": self._config.api_key,
                        "Content-Type": "application/json",
                        "X-Idempotency-Key": idempotency_key,
                    },
                )

            if response.status_code == 200:
                data = response.json()
                if data.get("errors"):
                    return ToolResult(
                        status="failed",
                        error=str(data["errors"]),
                        retryable=False,
                    )
                issue = data.get("data", {}).get("issueCreate", {}).get("issue", {})
                return ToolResult(
                    status="sent",
                    external_id=issue.get("identifier") or issue.get("id"),
                )
            elif response.status_code == 429:
                return ToolResult(status="failed", error="Rate limited", retryable=True)
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
            return ToolResult(
                status="unknown",
                error="Request timed out — will attempt reconcile",
                retryable=False,
            )
        except httpx.ConnectError:
            return ToolResult(status="failed", error="Connection failed", retryable=True)
        except Exception as exc:
            return ToolResult(status="failed", error=str(exc), retryable=False)

    def _build_description(self, payload: dict[str, Any], idempotency_key: str) -> str:
        """Build a Linear issue description with idempotency marker."""
        return (
            f"**Category:** {payload.get('category', 'N/A')}\n"
            f"**Priority:** {payload.get('priority', 'N/A')}\n"
            f"**Priority reason:** {payload.get('priority_reason', '')}\n"
            f"**Summary:** {payload.get('summary', '')}\n"
            f"**Suggested Action:** {payload.get('suggested_action', '')}\n"
            f"\n---\n_idempotency_key: {idempotency_key}_"
        )

    async def health(self) -> ToolHealthStatus:
        """Check if the Linear API is reachable."""
        try:
            query = "{ viewer { id } }"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self._config.api_url,
                    json={"query": query},
                    headers={"Authorization": self._config.api_key},
                )
            if response.status_code == 200:
                return ToolHealthStatus(healthy=True, breaker_state="closed")
            return ToolHealthStatus(
                healthy=False, breaker_state="closed", detail=f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return ToolHealthStatus(healthy=False, breaker_state="closed", detail=str(exc))

    def map_from_analysis(self, enquiry: Any, analysis: Any) -> dict[str, Any]:
        """Map enquiry + analysis to a Linear payload."""
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
        """Search Linear for an issue with our idempotency_key marker (Fix 8)."""
        query = """
        query SearchIssues($query: String!) {
            issues(filter: { description: { contains: $query } }) {
                nodes { id identifier }
            }
        }
        """

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    self._config.api_url,
                    json={"query": query, "variables": {"query": idempotency_key}},
                    headers={"Authorization": self._config.api_key},
                )

            if response.status_code == 200:
                data = response.json()
                issues = data.get("data", {}).get("issues", {}).get("nodes", [])
                if issues:
                    issue = issues[0]
                    return ToolResult(
                        status="sent",
                        external_id=issue.get("identifier") or issue.get("id"),
                    )
                # Not found — safe to retry
                return ToolResult(
                    status="failed",
                    error="Reconcile: issue not found",
                    retryable=True,
                )
        except Exception as exc:
            logger.debug("linear.reconcile_error", extra={"error": str(exc)})

        return None
