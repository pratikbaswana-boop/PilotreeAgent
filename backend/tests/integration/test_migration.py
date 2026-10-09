"""Test migration applies to an empty DB and downgrades cleanly."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.asyncio


async def test_migration_upgrade_downgrade(test_db: None) -> None:
    """Migration 0002 applies and downgrades cleanly on a fresh DB."""
    # test_db fixture already runs `alembic upgrade head` (0001 + 0002)
    # Verify all tables exist
    engine = create_async_engine(
        "postgresql+asyncpg://triage:triage_dev@localhost:5432/triage_test"
    )
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema='public' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in result.fetchall()}

    expected_tables = {
        "users",
        "enquiries",
        "analyses",
        "safety_verdicts",
        "reviews",
        "actions",
        "action_attempts",
        "tool_configs",
        "audit_log",
        "idempotency_keys",
        "jobs",
        "rate_limit_buckets",
        "breaker_state",
        "events",
        "alembic_version",
    }
    assert tables == expected_tables, f"Missing tables: {expected_tables - tables}"

    # Verify enum types
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT typname FROM pg_type WHERE typtype='e'"
                " AND typnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')"
                " ORDER BY typname"
            )
        )
        types = {row[0] for row in result.fetchall()}

    expected_types = {
        "user_role",
        "priority",
        "category",
        "decision",
        "action_status",
        "job_status",
    }
    assert types == expected_types

    await engine.dispose()


async def test_migration_downgrade_to_baseline(test_db: None) -> None:
    """Downgrade to 0001 leaves only users + user_role."""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("prepend_sys_path", str(BACKEND_DIR))

    await asyncio.to_thread(command.downgrade, alembic_cfg, "0001")

    engine = create_async_engine(
        "postgresql+asyncpg://triage:triage_dev@localhost:5432/triage_test"
    )
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema='public' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in result.fetchall()}

    assert tables == {"users", "alembic_version"}

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT typname FROM pg_type WHERE typtype='e'"
                " AND typnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')"
            )
        )
        types = {row[0] for row in result.fetchall()}

    assert types == {"user_role"}

    # Re-apply for other tests
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    await engine.dispose()


# Path constant
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
