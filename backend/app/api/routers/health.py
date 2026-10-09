"""Health endpoints (§4.2 API table).

- GET /healthz  — public, internal network; detailed status.
- GET /readyz   — public, internal network; 200 or 503 for LB probe.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.domain.models import BreakerState
from app.observability.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger()


async def _check_db(request: Request) -> bool:
    db = request.app.state.db
    try:
        async with db.session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("health.db_check_failed")
        return False


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    db_ok = await _check_db(request)
    breakers = {}
    if db_ok:
        async with request.app.state.db.session_factory() as session:
            breakers = {
                b.breaker_key: b.state for b in (await session.scalars(select(BreakerState))).all()
            }
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "breakers": breakers,
        "listen_connected": False,
    }


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    db_ok = await _check_db(request)
    # LISTEN readiness (M7) is not yet tracked; only DB readiness gates M1.
    ready = db_ok
    if not ready:
        return JSONResponse({"ready": False}, status_code=503)
    return JSONResponse({"ready": True})
