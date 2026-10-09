"""Authenticated SSE with subscribe-before-replay and bounded connections."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.engine import make_url

from app.security.auth import get_current_user

router = APIRouter(tags=["sse"])


def _format_sse(event_data: dict[str, Any]) -> bytes:
    lines = []
    if "id" in event_data:
        lines.append(f"id: {event_data['id']}")
    if "event" in event_data:
        lines.append(f"event: {event_data['event']}")
    if "data" in event_data:
        lines.append(f"data: {json.dumps(event_data['data'])}")
    return ("\n".join(lines) + "\n\n").encode()


@router.get("/sse")
async def sse_stream(
    request: Request, topics: str = Query(...), last_event_id: int | None = None
) -> StreamingResponse:
    async with request.app.state.db.session_factory() as db:
        await get_current_user(request, db)
        await db.commit()
    topic_list = list(dict.fromkeys(t.strip() for t in topics.split(",") if t.strip()))
    if not topic_list or len(topic_list) > 200:
        raise HTTPException(400, "Between 1 and 200 topics are required.")
    try:
        cursor = int(request.headers.get("Last-Event-ID", last_event_id or 0))
    except ValueError:
        raise HTTPException(400, "Invalid Last-Event-ID") from None
    settings = request.app.state.settings
    dsn = make_url(settings.database_session_pool_url).set(drivername="postgresql")

    async def stream():
        nonlocal cursor
        conn = await asyncpg.connect(dsn.render_as_string(hide_password=False))
        wake = asyncio.Event()

        def notify(*args):
            wake.set()

        channels = [t.replace(":", "_").replace("-", "_") for t in topic_list]
        try:
            for channel in channels:
                await conn.add_listener(channel, notify)
            if not cursor:
                cursor = await conn.fetchval("SELECT COALESCE(max(id), 0) FROM events")
            else:
                oldest = await conn.fetchval("SELECT min(id) FROM events")
                if oldest and cursor < oldest - 1:
                    yield _format_sse({"event": "resync", "data": {"topics": topic_list}})
            yield _format_sse({"event": "connected", "data": {"topics": topic_list}})
            started = time.monotonic()
            while not await request.is_disconnected():
                wake.clear()
                rows = await conn.fetch(
                    "SELECT id, type, payload FROM events WHERE topic = ANY($1::text[])"
                    " AND id > $2 ORDER BY id LIMIT 500",
                    topic_list,
                    cursor,
                )
                for row in rows:
                    cursor = row["id"]
                    yield _format_sse(
                        {"id": cursor, "event": row["type"], "data": json.loads(row["payload"])}
                    )
                if time.monotonic() - started >= settings.sse_max_connection_seconds:
                    break
                if len(rows) == 500:
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=15)
                except TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            await conn.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
