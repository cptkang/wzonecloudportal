"""FastAPI 앱 팩토리와 진입점 (계획 08 §8).

**어댑터 팩토리를 여기(또는 `api/deps.py`)서 구성한다** — `api`/`entry` 계층만
`src.infrastructure.vcenter|hyperv`를 import할 수 있다 (계획 03 §7).
"""

from __future__ import annotations

import logging
import logging.config
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from src.api.errors import install_error_handlers
from src.api.routes import auth, connections, health, users, virtual_machines
from src.config import Settings, get_settings
from src.domain.auth import Role, UserStatus
from src.infrastructure.db.engine import create_engine, create_session_factory
from src.infrastructure.repository.user_repo import UserRepository
from src.infrastructure.security.masking import install_masking
from src.infrastructure.security.password import hash_password

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
API_PREFIX = "/api/v1"


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # 자격증명 마스킹은 **로거가 아니라 핸들러**에 붙인다 (계획 10 §5.1)
    install_masking(logging.getLogger().handlers)


async def ensure_bootstrap_admin(settings: Settings, session_factory) -> None:  # type: ignore[no-untyped-def]
    """최초 기동 시 관리자 계정을 생성한다 (계획 09 §7).

    하드코딩된 기본 비밀번호를 두지 않는다. 환경변수가 없으면 생성하지 않고 경고만 남긴다.
    **비밀번호는 로그에 남기지 않는다.**

    워커를 여러 개 띄우면 lifespan이 워커마다 실행되어 이 함수가 **동시에** 호출된다.
    빈 DB에서는 모두 `count() == 0`을 보고 같은 계정을 만들려 하므로,
    `username` UNIQUE 제약 위반을 정상 경로로 처리한다 — 먼저 만든 워커가 이겼을 뿐이다.
    """
    async with session_factory() as session:
        users = UserRepository(session)
        if await users.count() > 0:
            return
        if not settings.bootstrap_admin_username or not settings.bootstrap_admin_password:
            logger.warning(
                "관리자 계정이 없습니다. "
                "PORTAL_BOOTSTRAP_ADMIN_USERNAME/PASSWORD를 설정하고 재기동하세요."
            )
            return
        try:
            await users.add(
                username=settings.bootstrap_admin_username.strip().lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                display_name="Administrator",
                role=Role.ADMIN,
                status=UserStatus.ACTIVE,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info("부트스트랩 관리자 계정이 이미 생성되어 있습니다 (동시 기동).")
            return
        logger.info(
            "부트스트랩 관리자 계정을 생성했습니다. 생성 후 환경변수를 제거하세요.",
            extra={"username": settings.bootstrap_admin_username},
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await ensure_bootstrap_admin(settings, session_factory)
        yield
        await engine.dispose()

    app = FastAPI(title="가상자원 인벤토리 포탈 API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory

    install_error_handlers(app)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(connections.router, prefix=API_PREFIX)
    app.include_router(virtual_machines.router, prefix=API_PREFIX)

    if STATIC_DIR.is_dir():
        # 라우터 등록 뒤에 마운트해야 /api/v1/*이 정적 파일에 가리지 않는다
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
