"""연결 관리 라우터 (계획 08 §5) — 모두 admin 전용 (NFR-210).

**하이퍼바이저 자원을 변경하는 엔드포인트는 존재하지 않는다** (D-005).
`[삭제]`·`[지금 수집]`은 **포탈의 연결 레코드**에 대한 조작이다.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.deps import (
    AppSettings,
    DbSession,
    Scope,
    client_ip,
    create_reader_factory,
    get_connection_service,
    require,
)
from src.api.schemas.connection import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionTestResponse,
)
from src.application.collect_service import CollectService
from src.application.connection_service import ConnectionCreateInput, ConnectionService
from src.config import Settings
from src.domain.auth import Permission
from src.domain.connection import Connection
from src.domain.enums import ConnectionStatus
from src.domain.exceptions import NotFoundError
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository
from src.infrastructure.security.cipher import CredentialCipher
from src.infrastructure.security.keys import EnvKeyProvider
from src.infrastructure.security.masking import sanitize_error

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/connections",
    tags=["connections"],
    dependencies=[Depends(require(Permission.CONNECTION_MANAGE))],
)

#: 진행 중인 수집 — **단일 프로세스 범위**다.
#:
#: UI가 버튼을 비활성화하지만 그것만으로는 다른 탭·다른 관리자·API 직접 호출을 막지
#: 못한다. 같은 연결을 동시에 수집하면 두 트랜잭션이 같은 VM을 insert하려다
#: `uq_vm_native` 제약에 걸려 **정상 수집이 실패로 뒤집힌다.**
#:
#: **워커를 여러 개 띄우면 성립하지 않는다** — 프로세스마다 별도 집합이다.
#: Step 3에서 Redis 분산 락으로 교체될 자리다 (ROADMAP §18).
_running_collections: set[UUID] = set()


def _to_input(payload: ConnectionCreate) -> ConnectionCreateInput:
    # protocol/auth_method/session_configuration은 WinRM 계열 스키마에만 있다 (계획 05 §4)
    return ConnectionCreateInput(
        display_name=payload.display_name,
        address=payload.address,
        port=payload.port,
        username=payload.username,
        password=payload.password,
        verify_tls=payload.verify_tls,
        kind=payload.kind,
        protocol=getattr(payload, "protocol", "https"),
        auth_method=getattr(payload, "auth_method", None),
        session_configuration=getattr(payload, "session_configuration", None),
    )


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    scope: Scope, svc: Annotated[ConnectionService, Depends(get_connection_service)]
) -> list[ConnectionResponse]:
    """목록 (**비밀번호 필드 없음**)."""
    return [
        ConnectionResponse.from_domain(conn, vm_count)
        for conn, vm_count in await svc.list_connections(scope)
    ]


@router.post("/test", response_model=ConnectionTestResponse)
async def test_unsaved_connection(
    payload: ConnectionCreate,
    request: Request,
    scope: Scope,
    session: DbSession,
    svc: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionTestResponse:
    """저장 전 연결 테스트 (FR-106). 자격증명을 저장하지 않는다.

    실패해도 **HTTP 200**이다 — 테스트 자체는 성공했고 결과가 실패인 것이다.
    """
    result = await svc.check_unsaved(scope, _to_input(payload), client_ip(request))
    await session.commit()
    return ConnectionTestResponse.from_domain(result)


@router.post("", status_code=201, response_model=ConnectionResponse)
async def create_connection(
    payload: ConnectionCreate,
    request: Request,
    scope: Scope,
    session: DbSession,
    svc: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    created = await svc.create(scope, _to_input(payload), client_ip(request))
    # 응답이 나가기 전에 커밋한다. UI가 등록 직후 목록을 새로고침하기 때문이다 (deps.get_db).
    await session.commit()
    return ConnectionResponse.from_domain(created)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: UUID,
    request: Request,
    scope: Scope,
    session: DbSession,
    svc: Annotated[ConnectionService, Depends(get_connection_service)],
) -> None:
    """포탈의 연결 레코드를 삭제한다. 수집된 VM이 있으면 422로 거부한다.

    FR-109의 2단계 확인(영향 범위 + 이름 입력)은 Step 3에서 구현한다.
    """
    await svc.delete(scope, connection_id, client_ip(request))
    await session.commit()


@router.post("/{connection_id}/collect", status_code=202)
async def start_collection(
    connection_id: UUID,
    background: BackgroundTasks,
    request: Request,
    scope: Scope,
    settings: AppSettings,
) -> dict[str, str]:
    """수집을 시작하고 **즉시 202로 반환한다** (ROADMAP §9.1).

    수천 건 수집은 수 분이 걸릴 수 있다. 동기 응답으로 만들면 프록시·브라우저
    타임아웃에 걸리고, 그때 수집은 계속 진행 중인데 UI는 실패로 표시되어 원인 파악이
    어려워진다. UI는 `GET /connections`를 폴링해 `last_success_at`·`last_error`로
    완료를 판정한다.

    이미 수집 중이면 **작업을 추가하지 않고** `already_running`을 돌려준다.
    오류가 아니므로 상태 코드는 202 그대로다 — 4xx로 만들면 UI가 오류로 표시한다.
    """
    scope.require(Permission.COLLECTION_TRIGGER)

    # 중복 판정과 등록 사이에 `await`를 두지 않는다. 사이에 제어가 넘어가면
    # 두 요청이 모두 "진행 중 아님"을 보고 통과한다.
    if connection_id in _running_collections:
        return {"status": "already_running"}
    _running_collections.add(connection_id)

    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    cipher = CredentialCipher(EnvKeyProvider(settings))

    try:
        # 요청 세션은 응답과 함께 닫히므로 백그라운드 작업은 자체 세션을 연다.
        async with factory() as session:
            connections = ConnectionRepository(session, cipher)
            conn = await connections.get(connection_id)
            if conn is None:
                raise NotFoundError("연결을 찾을 수 없습니다.")
            await connections.mark_attempt(connection_id)
            await session.commit()
    except Exception:
        _running_collections.discard(connection_id)  # 작업을 걸지 못했으면 되돌린다
        raise

    background.add_task(_run_collection, factory, settings, connection_id)
    return {"status": "accepted"}


async def _run_collection(
    factory: async_sessionmaker[AsyncSession], settings: Settings, connection_id: UUID
) -> None:
    """백그라운드 수집. 예외를 밖으로 던지지 않고 연결 상태에 기록한다.

    끝나면 **성공·실패 어느 쪽이든** 진행 중 표시를 푼다. 풀지 않으면 그 연결은
    프로세스가 살아 있는 동안 다시 수집할 수 없다.
    """
    cipher = CredentialCipher(EnvKeyProvider(settings))
    try:
        await _collect_once(factory, settings, cipher, connection_id)
    finally:
        _running_collections.discard(connection_id)


async def _collect_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    cipher: CredentialCipher,
    connection_id: UUID,
) -> None:
    conn: Connection | None = None
    async with factory() as session:
        connections = ConnectionRepository(session, cipher)
        vms = VirtualMachineRepository(session)
        service = CollectService(connections, vms, create_reader_factory(settings), settings)
        try:
            conn = await connections.load_with_password(connection_id)
            if conn is None:
                return
            summary = await service.collect(conn)
            await session.commit()
            # **`extra` 키에 LogRecord 예약 필드명을 쓰면 안 된다.** `created`는
            # LogRecord의 타임스탬프 속성이라 logging이 KeyError를 던진다.
            # 이 예외는 아래 `except`에 잡혀 **정상 수집이 전부 "수집 중 예외"로
            # 기록되던** 버그였다 (2026-08-19). ruff `G101`이 회귀를 막는다.
            logger.info(
                "수집 완료",
                extra={
                    "connection_id": str(connection_id),
                    "vm_created": summary.created,
                    "vm_updated": summary.updated,
                    "collect_failed": summary.failed,
                },
            )
        except Exception as exc:
            await session.rollback()
            logger.exception("수집 중 예외", extra={"connection_id": str(connection_id)})
            secrets = (conn.password.get_secret_value(),) if conn is not None else ()
            await _mark_unexpected_failure(factory, settings, connection_id, exc, secrets)


async def _mark_unexpected_failure(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    connection_id: UUID,
    exc: BaseException,
    secrets: tuple[str, ...],
) -> None:
    """예상하지 못한 예외를 연결 상태에 남긴다.

    `CollectService`는 자신이 아는 실패(인증·권한·도달 불가)를 이미 기록한다.
    이 함수는 **그 밖의 예외** — DB 오류·매핑 버그 등 포탈 쪽 문제 — 를 담당한다.

    남기지 않으면 UI 폴링이 `last_error`를 보지 못해 화면이 **"수집 중…"에서
    영원히 벗어나지 못한다** (`static/js/connections.js`의 `startPolling`).

    롤백된 세션으로는 기록할 수 없으므로 새 세션을 연다.
    **수집 데이터는 지우지 않는다** — 신선도만 오래된 채로 남는다 (NFR-302).
    """
    try:
        async with factory() as session:
            repo = ConnectionRepository(session, CredentialCipher(EnvKeyProvider(settings)))
            await repo.mark_failure(
                connection_id,
                f"수집 중 오류가 발생했습니다: {sanitize_error(exc, secrets=secrets)}",
                ConnectionStatus.COLLECTION_ERROR,
            )
            await session.commit()
    except Exception:
        logger.exception(
            "수집 실패 상태 기록에 실패", extra={"connection_id": str(connection_id)}
        )
