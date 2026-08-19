"""Alembic 환경 설정.

접속 URL은 `alembic.ini`가 아니라 애플리케이션 설정(`PORTAL_DATABASE_URL`)에서 읽는다.
두 곳에 두면 어긋나고, ini에 두면 자격증명이 저장소에 커밋된다.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from src.config import get_settings
from src.infrastructure.db.models import Base

config = context.config
if config.config_file_name is not None:
    # **`disable_existing_loggers=False`가 필수다.** 기본값(True)은 이 시점에 이미
    # 만들어진 로거를 **전부 비활성화**한다. 같은 프로세스에서 마이그레이션을 돌리면
    # (테스트, 또는 앱 기동 시 자동 업그레이드) 애플리케이션 로그가 통째로 사라진다.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
