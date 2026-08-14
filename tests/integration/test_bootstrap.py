"""부트스트랩 관리자 생성 (계획 09 §7).

워커를 여러 개 띄우면 lifespan이 워커마다 실행되어 이 함수가 동시에 호출된다.
배포 구성(`docs/05_deployment.md`)이 `--workers 2`를 권장하므로 경합을 검증한다.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from src.config import Settings, get_settings
from src.domain.auth import Role, UserStatus
from src.infrastructure.db.models import UserRow
from src.infrastructure.repository.user_repo import UserRepository
from src.infrastructure.security.password import verify_password
from src.main import ensure_bootstrap_admin

BOOTSTRAP_PASSWORD = "bootstrap-admin-password"


@pytest.fixture
def bootstrap_settings() -> Settings:
    get_settings.cache_clear()
    base = get_settings()
    return base.model_copy(
        update={
            "bootstrap_admin_username": "Portal-Admin",
            "bootstrap_admin_password": SecretStr(BOOTSTRAP_PASSWORD),
        }
    )


async def test_creates_admin_when_no_users_exist(session_factory, bootstrap_settings) -> None:
    await ensure_bootstrap_admin(bootstrap_settings, session_factory)

    async with session_factory() as session:
        row = (await session.execute(select(UserRow))).scalar_one()
        assert row.username == "portal-admin"  # 소문자 정규화
        assert row.role == Role.ADMIN.value
        assert row.status == UserStatus.ACTIVE.value
        assert verify_password(SecretStr(BOOTSTRAP_PASSWORD), row.password_hash)[0]
        assert BOOTSTRAP_PASSWORD not in row.password_hash


async def test_does_nothing_when_users_already_exist(session_factory, bootstrap_settings) -> None:
    """계정이 하나라도 있으면 만들지 않는다 — 재기동마다 되살아나면 안 된다."""
    async with session_factory() as session:
        await UserRepository(session).add(
            username="someone", password_hash="x", role=Role.VIEWER, status=UserStatus.ACTIVE
        )
        await session.commit()

    await ensure_bootstrap_admin(bootstrap_settings, session_factory)

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserRow)) == 1


async def test_concurrent_workers_do_not_crash(session_factory, bootstrap_settings) -> None:
    """`--workers 2`로 빈 DB에 기동해도 워커가 죽지 않아야 한다.

    UNIQUE 제약 위반을 예외로 흘리면 진 워커의 lifespan이 실패해 기동에 실패한다.
    """
    await asyncio.gather(
        *(ensure_bootstrap_admin(bootstrap_settings, session_factory) for _ in range(4))
    )

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserRow)) == 1


async def test_warns_instead_of_creating_when_env_missing(session_factory) -> None:
    """하드코딩된 기본 비밀번호를 두지 않는다."""
    get_settings.cache_clear()
    settings = get_settings().model_copy(
        update={"bootstrap_admin_username": None, "bootstrap_admin_password": None}
    )

    await ensure_bootstrap_admin(settings, session_factory)

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserRow)) == 0
