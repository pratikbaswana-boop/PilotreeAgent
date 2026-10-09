"""Event publisher for SSE fanout (§3.11).

Inserts an event row into the `events` table and sends a Postgres NOTIFY
so that SSE listeners on the same channel pick it up immediately.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Event


async def publish_event(
    db: AsyncSession,
    topic: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """Insert an event row and NOTIFY the channel.

    Args:
        db: async DB session (must be committed by caller)
        topic: e.g. "enquiry:{enquiry_id}"
        event_type: e.g. "analysis.updated", "job.failed", "action.updated"
        payload: event data as a dict

    Returns:
        The event id (serial).
    """
    # Insert the event row
    event = Event(topic=topic, type=event_type, payload=payload)
    db.add(event)
    await db.flush()  # get the id

    event_id = event.id

    # Send NOTIFY to the channel (payload is just the event id)
    # The channel name is the topic with ":" replaced by "_" for NOTIFY
    # safety (channel names are identifiers)
    channel = topic.replace(":", "_").replace("-", "_")
    await db.execute(
        text("SELECT pg_notify(:channel, :event_id)"),
        {"channel": channel, "event_id": str(event_id)},
    )

    return event_id
