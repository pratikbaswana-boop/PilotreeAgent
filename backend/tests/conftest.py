"""Shared test fixtures.

Requires a running Postgres at localhost:5432 (docker compose up).
Creates a test database, runs Alembic migrations, and provides an
authenticated httpx client with a static-key JWT verifier.
"""

# ── Set test env vars BEFORE any app imports ──────────────────────
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ["DATABASE_URL"] = "postgresql+asyncpg://triage:triage_dev@localhost:5432/triage_test"
os.environ["DATABASE_SESSION_POOL_URL"] = os.environ["DATABASE_URL"]
os.environ["OIDC_ISSUER"] = "https://test.example.com"
os.environ["OIDC_CLIENT_ID"] = "triage-test"
os.environ["OIDC_JWKS_URI"] = "https://test.example.com/jwks"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["LOCAL_DEVELOPMENT_AUTH"] = "false"
os.environ["LOCAL_DEVELOPMENT_SECRET"] = ""

# ── Now import app + test modules ─────────────────────────────────
import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from alembic.config import Config

ADMIN_DB_URL = "postgresql+asyncpg://triage:triage_dev@localhost:5432/triage"


# ── JWT test helpers ──────────────────────────────────────────────

class StaticKeyJWTVerifier:
    """Test-only verifier: checks JWTs against a static public key."""

    def __init__(self, public_pem: bytes, issuer: str, client_id: str) -> None:
        self._public_key = public_pem
        self._issuer = issuer
        self._client_id = client_id

    def verify(self, token: str) -> dict[str, Any]:
        return jwt.decode(  # type: ignore[no-any-return]
            token,
            self._public_key,
            algorithms=["RS256"],
            issuer=self._issuer,
            audience=self._client_id,
        )


def create_test_jwt(
    private_pem: bytes,
    *,
    sub: str = "test-subject-001",
    email: str = "reviewer@pilotree.example",
    name: str = "Test Reviewer",
    exp_seconds: int = 3600,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "name": name,
        "iss": "https://test.example.com",
        "aud": "triage-test",
        "iat": now,
        "exp": now + exp_seconds,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


# ── Session fixtures ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest_asyncio.fixture(scope="session")
async def test_db() -> AsyncIterator[None]:
    """Create triage_test, run migrations, yield, then drop it."""
    from app.config import get_settings

    get_settings.cache_clear()

    admin_engine = create_async_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS triage_test"))
        await conn.execute(text("CREATE DATABASE triage_test"))
    await admin_engine.dispose()

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield

    admin_engine = create_async_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname='triage_test' AND pid <> pg_backend_pid()"
            )
        )
        await conn.execute(text("DROP DATABASE IF EXISTS triage_test"))
    await admin_engine.dispose()


@pytest_asyncio.fixture
async def db_engine(test_db: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(
    rsa_key_pair: tuple[bytes, bytes],
) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    _, public_pem = rsa_key_pair
    async with LifespanManager(app):
        app.state.jwt_verifier = StaticKeyJWTVerifier(
            public_pem=public_pem,
            issuer="https://test.example.com",
            client_id="triage-test",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_engine: AsyncEngine) -> AsyncIterator[None]:
    """Truncate all tables between tests for isolation."""
    yield
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE action_attempts, actions, reviews, safety_verdicts, analyses,"
                " enquiries, idempotency_keys, events, jobs, rate_limit_buckets,"
                " breaker_state, tool_configs, audit_log, users CASCADE"
            )
        )


# ── Convenience fixtures ───────────────────────────────────────────

@pytest.fixture
def auth_token(rsa_key_pair: tuple[bytes, bytes]) -> str:
    private_pem, _ = rsa_key_pair
    return create_test_jwt(private_pem)


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def expired_token(rsa_key_pair: tuple[bytes, bytes]) -> str:
    private_pem, _ = rsa_key_pair
    return create_test_jwt(private_pem, exp_seconds=-10)


@pytest_asyncio.fixture
async def reviewer_headers(
    rsa_key_pair: tuple[bytes, bytes],
    db_engine: AsyncEngine,
) -> dict[str, str]:
    """Seed a reviewer user in the DB and return auth headers for it."""
    import uuid as _uuid

    from app.domain.models import User

    private_pem, _ = rsa_key_pair
    token = create_test_jwt(
        private_pem,
        sub="test-reviewer-001",
        email="reviewer@pilotree.example",
        name="Test Reviewer",
    )

    async with db_engine.begin() as conn:
        from sqlalchemy import select
        result = await conn.execute(
            select(User).where(User.oidc_subject == "test-reviewer-001")
        )
        if result.scalar_one_or_none() is None:
            await conn.execute(
                text(
                    "INSERT INTO users (id, oidc_subject, email, display_name, role)"
                    " VALUES (:id, 'test-reviewer-001', 'reviewer@pilotree.example',"
                    " 'Test Reviewer', 'reviewer')"
                ),
                {"id": str(_uuid.uuid4())},
            )

    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_headers(
    rsa_key_pair: tuple[bytes, bytes],
    db_engine: AsyncEngine,
) -> dict[str, str]:
    """Seed an admin user in the DB and return auth headers for it."""
    import uuid as _uuid

    from app.domain.models import User

    private_pem, _ = rsa_key_pair
    token = create_test_jwt(
        private_pem,
        sub="test-admin-001",
        email="admin@pilotree.example",
        name="Test Admin",
    )

    async with db_engine.begin() as conn:
        from sqlalchemy import select
        result = await conn.execute(
            select(User).where(User.oidc_subject == "test-admin-001")
        )
        if result.scalar_one_or_none() is None:
            await conn.execute(
                text(
                    "INSERT INTO users (id, oidc_subject, email, display_name, role)"
                    " VALUES (:id, 'test-admin-001', 'admin@pilotree.example',"
                    " 'Test Admin', 'admin')"
                ),
                {"id": str(_uuid.uuid4())},
            )

    return {"Authorization": f"Bearer {token}"}
