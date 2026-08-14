"""테스트 공통 픽스처.

통합 테스트는 **실제 PostgreSQL**을 쓴다. JSONB·INET·부분 인덱스·pg_trgm이
스키마에 들어 있어 SQLite로는 검증되지 않는다 (D-013).

스키마는 **Alembic으로 만든다** — 매 실행마다 마이그레이션 자체가 검증되고
모델과 마이그레이션의 드리프트가 드러난다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("PORTAL_DATABASE_URL", "postgresql+asyncpg://portal:devpass@127.0.0.1:55432/wzoneportal")
os.environ.setdefault("PORTAL_JWT_SECRET", "test-secret-value-for-pytest-only")
# base64('pytest-only-key-32bytes-padding!') — 실제 자격증명이 아니다 (계획 10 §10)
os.environ.setdefault(
    "PORTAL_CREDENTIAL_ENCRYPTION_KEY", "cHl0ZXN0LW9ubHkta2V5LTMyYnl0ZXMtcGFkZGluZyE="
)
os.environ.setdefault("PORTAL_COOKIE_SECURE", "false")
# 부트스트랩 관리자는 테스트에서 직접 만든다
os.environ.pop("PORTAL_BOOTSTRAP_ADMIN_USERNAME", None)
os.environ.pop("PORTAL_BOOTSTRAP_ADMIN_PASSWORD", None)

TEST_DB_NAME = "wzoneportal_test"


def _test_database_url() -> str:
    base = os.environ["PORTAL_DATABASE_URL"]
    head, _, _ = base.rpartition("/")
    return f"{head}/{TEST_DB_NAME}"


@pytest.fixture(scope="session")
def database_url() -> str:
    return _test_database_url()


@pytest.fixture(scope="session", autouse=True)
def _prepare_database(database_url: str) -> None:
    """테스트 DB를 새로 만들고 Alembic으로 스키마를 적용한다."""
    import asyncio

    import asyncpg

    parts = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://"))

    async def recreate() -> None:
        conn = await asyncpg.connect(
            host=parts.hostname,
            port=parts.port,
            user=parts.username,
            password=parts.password,
            database="postgres",
        )
        try:
            await conn.execute(
                f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'
            )
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        finally:
            await conn.close()

    asyncio.run(recreate())

    os.environ["PORTAL_DATABASE_URL"] = database_url
    from src.config import get_settings

    get_settings.cache_clear()

    config = Config("alembic.ini")
    command.upgrade(config, "head")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator:  # type: ignore[type-arg]
    eng = create_async_engine(database_url, poolclass=None)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[no-untyped-def]
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    async with session_factory() as s:
        yield s
        await s.rollback()


@pytest.fixture(autouse=True)
async def _clean_tables(engine) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    """테스트 간 데이터 격리. 스키마는 유지하고 행만 지운다."""
    yield
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE audit_events, user_connection_scopes, users, "
                "resource_identities, virtual_machines, connections RESTART IDENTITY CASCADE"
            )
        )
