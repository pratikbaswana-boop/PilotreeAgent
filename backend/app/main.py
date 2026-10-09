"""FastAPI app factory with lifespan (§3.1 main.py).

Lifespan startup: configure logging, load settings, create DB engines,
create JWT verifier.  Shutdown: dispose engines.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import actions, admin, analyses, auth, enquiries, health, sse
from app.config import Settings, get_settings
from app.infra.db import DBManager, create_db
from app.observability.logging import CorrelationIdMiddleware, configure_logging
from app.security.auth import JWKSJWTVerifier


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings: Settings = get_settings()
    app.state.settings = settings
    app.state.db = create_db(settings)
    app.state.jwt_verifier = JWKSJWTVerifier(
        jwks_uri=settings.oidc_jwks_uri,
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        leeway=settings.jwt_clock_skew_seconds,
    )
    if settings.local_development_auth:
        from app.security.local_auth import LocalJWTVerifier

        app.state.jwt_verifier = LocalJWTVerifier(settings.local_development_secret)
    yield
    db: DBManager = app.state.db
    await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Logistics Enquiry Triage Tool",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(enquiries.router)
    app.include_router(analyses.router)
    app.include_router(actions.router)
    app.include_router(sse.router)
    app.include_router(admin.router)
    return app


app = create_app()
