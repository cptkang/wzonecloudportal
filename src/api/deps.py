"""DI와 인증·범위 의존성 (계획 09 §6, 계획 03 §7).

**어댑터 팩토리를 여기서 구성한다.** `api`/`entry` 계층만
`src.infrastructure.vcenter|hyperv`를 import할 수 있다 (arch-check 특화 규칙 A).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.auth_service import AuthService
from src.application.collect_service import CollectService
from src.application.connection_service import ConnectionService
from src.application.inventory_query import InventoryQueryService
from src.application.user_service import RegistrationService, UserAdminService
from src.config import Settings, get_settings
from src.domain.auth import AccessScope, AuthenticatedUser, Permission, Role, build_scope
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind
from src.domain.exceptions import AuthenticationError
from src.domain.ports import HypervisorInventoryReader
from src.infrastructure.repository.audit_repo import AuditRepository
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.user_repo import ScopeRepository, UserRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository
from src.infrastructure.security.cipher import CredentialCipher
from src.infrastructure.security.keys import EnvKeyProvider
from src.infrastructure.security.tokens import COOKIE_NAME, decode_access_token

#: 인증이 필요 없는 경로 — **화이트리스트다.**
#: 새 엔드포인트는 기본이 인증 필요이며, 공개가 필요하면 여기에 명시적으로 추가한다.
#: 반대로 만들면 인증 누락이 조용히 생긴다 (계획 08 §4.2).
#:
#: `tests/integration/test_api.py::test_every_route_outside_the_whitelist_requires_auth`가
#: 이 목록을 강제한다 — 여기 없는 `/api/v1` 경로가 401을 주지 않으면 실패한다.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/register"}
)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """요청당 세션 1개.

    **쓰기 엔드포인트는 핸들러 안에서 명시적으로 커밋한다.** 여기 teardown의 커밋은
    응답이 나간 뒤에 실행되므로, 클라이언트가 곧바로 이어서 조회하면 방금 쓴 값이
    보이지 않는다 (UI의 "등록 → 목록 새로고침" 흐름이 실제로 그렇다).
    아래 커밋은 누락에 대비한 안전망이며 순서를 보장하지 않는다.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_cipher(settings: AppSettings) -> CredentialCipher:
    return CredentialCipher(EnvKeyProvider(settings))


def create_reader_factory(settings: Settings) -> Callable[[Connection], HypervisorInventoryReader]:
    """연결 유형에 맞는 어댑터를 고른다 (계획 03 §7, D-012).

    **하이퍼바이저 분기는 이 함수에만 존재한다.** 유스케이스 코드에 분기가 스며들면
    arch-check 특화 규칙 위반이다. import를 함수 안에 두어 pyVmomi/pypsrp 로딩을
    실제 사용 시점으로 미룬다.
    """

    def factory(conn: Connection) -> HypervisorInventoryReader:
        if conn.kind is ConnectionKind.SCVMM:
            from src.infrastructure.hyperv import ScvmmInventoryReader

            return ScvmmInventoryReader(conn, settings)
        if conn.kind in (ConnectionKind.HYPERV_HOST, ConnectionKind.HYPERV_CLUSTER):
            from src.infrastructure.hyperv import HyperVHostInventoryReader

            return HyperVHostInventoryReader(conn, settings)
        from src.infrastructure.vcenter import VCenterInventoryReader

        return VCenterInventoryReader(conn, settings)

    return factory


# ── 저장소 ───────────────────────────────────────────────────


def get_user_repo(session: DbSession) -> UserRepository:
    return UserRepository(session)


def get_scope_repo(session: DbSession) -> ScopeRepository:
    return ScopeRepository(session)


def get_audit_repo(session: DbSession) -> AuditRepository:
    return AuditRepository(session)


def get_vm_repo(session: DbSession) -> VirtualMachineRepository:
    return VirtualMachineRepository(session)


def get_connection_repo(
    session: DbSession, cipher: Annotated[CredentialCipher, Depends(get_cipher)]
) -> ConnectionRepository:
    return ConnectionRepository(session, cipher)


# ── 인증 ─────────────────────────────────────────────────────


async def get_current_user(
    request: Request,
    users: Annotated[UserRepository, Depends(get_user_repo)],
    settings: AppSettings,
) -> AuthenticatedUser:
    token = request.cookies.get(COOKIE_NAME)  # 브라우저 세션은 쿠키다 (D-014)
    if not token:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    try:
        claims = decode_access_token(token, settings)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다. 다시 로그인하세요.") from None

    user = await users.get(claims.sub)
    # 상태를 매 요청 재확인한다. 토큰이 서명상 유효해도 계정이 이미 비활성화·거부되었을 수
    # 있다. 이 검사가 없으면 권한 회수가 토큰 만료까지 지연된다 (계획 09 §6).
    if user is None or not user.can_sign_in:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다. 다시 로그인하세요.")
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def get_active_user(request: Request, user: CurrentUser) -> AuthenticatedUser:
    """임시 비밀번호 사용자는 변경 전까지 조회 API를 호출할 수 없다 (FR-1008)."""
    if user.must_change_password and request.url.path not in {
        "/api/v1/auth/me",
        "/api/v1/auth/change-password",
        "/api/v1/auth/logout",
    }:
        raise HTTPException(status_code=403, detail="비밀번호를 먼저 변경해야 합니다.")
    return user


ActiveUser = Annotated[AuthenticatedUser, Depends(get_active_user)]


async def get_access_scope(
    user: ActiveUser, scopes: Annotated[ScopeRepository, Depends(get_scope_repo)]
) -> AccessScope:
    """요청마다 DB에서 범위를 조회한다.

    토큰에 넣지 않는 이유는 범위 변경이 토큰 만료까지 지연되면 권한 회수가 늦어지기
    때문이다 (계획 09 §4.2).
    """
    if user.role is Role.ADMIN:
        return build_scope(user, ())
    return build_scope(user, await scopes.list_for_user(user.user_id))


Scope = Annotated[AccessScope, Depends(get_access_scope)]


def require(*permissions: Permission) -> Callable[..., AuthenticatedUser]:
    """라우터용 권한 검사. 유스케이스에서도 재검사한다 (이중 방어, 계획 09 §6.1)."""

    async def _check(user: ActiveUser) -> AuthenticatedUser:
        for p in permissions:
            if not user.has(p):
                raise HTTPException(status_code=403, detail=f"권한이 없습니다: {p.value}")
        return user

    return _check


# ── 서비스 ───────────────────────────────────────────────────


def get_auth_service(
    users: Annotated[UserRepository, Depends(get_user_repo)],
    audit: Annotated[AuditRepository, Depends(get_audit_repo)],
    settings: AppSettings,
) -> AuthService:
    return AuthService(users, audit, settings)


def get_registration_service(
    users: Annotated[UserRepository, Depends(get_user_repo)],
    audit: Annotated[AuditRepository, Depends(get_audit_repo)],
) -> RegistrationService:
    return RegistrationService(users, audit)


def get_user_admin_service(
    request: Request,
    users: Annotated[UserRepository, Depends(get_user_repo)],
    scopes: Annotated[ScopeRepository, Depends(get_scope_repo)],
    audit: Annotated[AuditRepository, Depends(get_audit_repo)],
) -> UserAdminService:
    return UserAdminService(users, scopes, audit, actor_ip=client_ip(request))


def get_connection_service(
    repo: Annotated[ConnectionRepository, Depends(get_connection_repo)],
    vms: Annotated[VirtualMachineRepository, Depends(get_vm_repo)],
    audit: Annotated[AuditRepository, Depends(get_audit_repo)],
    cipher: Annotated[CredentialCipher, Depends(get_cipher)],
    settings: AppSettings,
) -> ConnectionService:
    return ConnectionService(repo, vms, audit, cipher, create_reader_factory(settings))


def get_inventory_service(
    vms: Annotated[VirtualMachineRepository, Depends(get_vm_repo)],
) -> InventoryQueryService:
    return InventoryQueryService(vms)


def get_collect_service(
    connections: Annotated[ConnectionRepository, Depends(get_connection_repo)],
    vms: Annotated[VirtualMachineRepository, Depends(get_vm_repo)],
    settings: AppSettings,
) -> CollectService:
    return CollectService(connections, vms, create_reader_factory(settings), settings)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
