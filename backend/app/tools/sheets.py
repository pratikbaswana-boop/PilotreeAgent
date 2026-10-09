"""Google Sheets tool integration (§3.2).

Appends a row to a Google Sheet via the Sheets API.
Sheets supports a reliable lookup by a hidden idempotency-key column,
so reconcile() can resolve ambiguous outcomes (Fix 8).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.tools.protocol import ToolHealthStatus, ToolResult, ToolUIMetadata

logger = logging.getLogger(__name__)


class SheetsConfig(BaseModel):
    """Configuration for the Sheets tool."""

    api_key: str
    spreadsheet_id: str
    sheet_name: str = "Triage"
    api_url: str = "https://sheets.googleapis.com/v4/spreadsheets"


class SheetsTool:
    """Google Sheets tool — appends rows."""

    name = "sheets"
    config_schema = SheetsConfig
    required_scopes: set[str] = {"write"}
    ui_metadata = ToolUIMetadata(
        key="sheets",
        label="Google Sheets",
        icon="sheets",
        suggested_for=["low", "medium"],
        payload_preview_fields=["summary", "category", "priority"],
        enabled=True,
    )

    def __init__(self, config: SheetsConfig) -> None:
        self._config = config

    async def validate(self, payload: dict[str, Any]) -> None:
        """Validate the payload before sending."""
        if not payload.get("summary"):
            raise ValueError("Payload must contain 'summary'")

    async def execute(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult:
        """Append a row to the Google Sheet."""
        range_name = f"{self._config.sheet_name}!A:Z"
        url = (
            f"{self._config.api_url}/{self._config.spreadsheet_id}"
            f"/values/{range_name}:append"
            f"?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
            f"&key={self._config.api_key}"
        )

        row = [
            idempotency_key,
            payload.get("enquiry_id", ""),
            payload.get("customer_name", ""),
            payload.get("company", ""),
            payload.get("summary", ""),
            payload.get("category", ""),
            payload.get("priority", ""),
            payload.get("suggested_action", ""),
            payload.get("reason", ""),
        ]

        body = {"values": [row]}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=2)) as client:
                response = await client.post(url, json=body)

            if response.status_code == 200:
                data = response.json()
                updates = data.get("updates", {})
                return ToolResult(
                    status="sent",
                    external_id=updates.get("updatedRange"),
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

    async def health(self) -> ToolHealthStatus:
        """Check if the Sheets API is reachable."""
        try:
            url = (
                f"{self._config.api_url}/{self._config.spreadsheet_id}"
                f"?fields=spreadsheetId&key={self._config.api_key}"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
            if response.status_code == 200:
                return ToolHealthStatus(healthy=True, breaker_state="closed")
            return ToolHealthStatus(
                healthy=False, breaker_state="closed", detail=f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return ToolHealthStatus(healthy=False, breaker_state="closed", detail=str(exc))

    def map_from_analysis(self, enquiry: Any, analysis: Any) -> dict[str, Any]:
        """Map enquiry + analysis to a Sheets payload."""
        return {
            "summary": getattr(analysis, "summary", ""),
            "category": getattr(analysis, "category", ""),
            "priority": getattr(analysis, "priority", ""),
            "suggested_action": getattr(analysis, "suggested_action", ""),
            "reason": getattr(analysis, "reason", ""),
            "enquiry_id": getattr(enquiry, "id", ""),
            "customer_name": getattr(enquiry, "name", ""),
            "company": getattr(enquiry, "company", ""),
        }

    async def reconcile(self, payload: dict[str, Any], idempotency_key: str) -> ToolResult | None:
        """Search the sheet for a row with our idempotency_key (Fix 8)."""
        range_name = f"{self._config.sheet_name}!A:A"
        url = (
            f"{self._config.api_url}/{self._config.spreadsheet_id}"
            f"/values/{range_name}?key={self._config.api_key}"
        )

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url)

            if response.status_code == 200:
                data = response.json()
                values = data.get("values", [])
                for row in values:
                    if row and row[0] == idempotency_key:
                        return ToolResult(
                            status="sent",
                            external_id=f"row:{idempotency_key}",
                        )
                # Not found — safe to retry
                return ToolResult(
                    status="failed",
                    error="Reconcile: row not found",
                    retryable=True,
                )
        except Exception as exc:
            logger.debug("sheets.reconcile_error", extra={"error": str(exc)})

        return None
