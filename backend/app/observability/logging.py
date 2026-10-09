"""Structured logging via structlog.

Features (§3.1 observability/logging.py):
  - JSON output for machine consumption.
  - Correlation ID from `X-Request-ID` header (or generated) bound via
    contextvars so every log line in a request carries it.
  - PII redaction processor: redacts values for keys matching known PII
    field names (email, message, phone, token, etc.).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

# Keys whose values are redacted in log events (case-insensitive substring match).
_PII_KEYS = frozenset(
    {
        "email",
        "message",
        "phone",
        "token",
        "password",
        "secret",
        "api_key",
        "credential",
        "authorization",
        "oidc_subject",
        "raw",
    }
)


def _redact_pii(
    _logger: Any, _method_name: str, event_dict: Any
) -> Any:
    """structlog processor: redact values whose key matches a PII pattern."""
    for key in list(event_dict):
        if any(pii in key.lower() for pii in _PII_KEYS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    """Configure structlog once at startup."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO+
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()  # type: ignore[no-any-return]


class CorrelationIdMiddleware:
    """ASGI middleware: extract or generate X-Request-ID, bind to log context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Echo the correlation ID back in the response.
        async def send_with_id(message: Any) -> None:
            if message["type"] == "http.response.start":
                response_headers: Iterable[tuple[bytes, bytes]] = message.get(
                    "headers", []
                )
                message["headers"] = [
                    *response_headers,
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        await self.app(scope, receive, send_with_id)
