"""연결 등록·삭제·연결 테스트 (계획 08 §5의 Step 1 축소판).

연결 수정(PATCH)은 Step 3이다. 주소 변경 경고(FR-110·111)와 비밀번호 부분 갱신(FR-108)이
얽혀 있어 계획 08 §5.5 전체가 딸려 온다. MVP에서는 **삭제 후 재등록**으로 대체한다
(ROADMAP §5.2).

이 서비스는 어댑터를 직접 import하지 않고 `ReaderFactory`를 주입받는다 (계획 03 §7).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import SecretStr

from src.domain.audit import AuditAction, AuditEvent
from src.domain.auth import AccessScope, Permission
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind, WinRmAuth
from src.domain.exceptions import DuplicateError, NotFoundError, ValidationError
from src.domain.ports import ConnectionCheckResult, ReaderFactory
from src.infrastructure.repository.audit_repo import AuditRepository, build_detail
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository
from src.infrastructure.security.cipher import CredentialCipher


@dataclass(frozen=True, slots=True)
class ConnectionCreateInput:
    display_name: str
    address: str
    port: int
    username: str
    password: SecretStr
    verify_tls: bool
    kind: ConnectionKind = ConnectionKind.VCENTER
    #: WinRM 연결 전용 (계획 05 §4). vCenter는 기본값 그대로 둔다.
    protocol: str = "https"
    auth_method: WinRmAuth | None = None
    session_configuration: str | None = None


def _to_connection(payload: ConnectionCreateInput) -> Connection:
    """입력 → 도메인 연결. 등록과 저장 전 테스트가 같은 구성을 쓴다."""
    return Connection(
        connection_id=uuid4(),
        kind=payload.kind,
        display_name=payload.display_name,
        address=payload.address,
        port=payload.port,
        username=payload.username,
        password=payload.password,
        protocol="http" if payload.protocol == "http" else "https",
        auth_method=payload.auth_method,
        session_configuration=payload.session_configuration,
        verify_tls=payload.verify_tls,
    )


class ConnectionService:
    def __init__(
        self,
        repo: ConnectionRepository,
        vms: VirtualMachineRepository,
        audit: AuditRepository,
        cipher: CredentialCipher,
        reader_factory: ReaderFactory,
    ) -> None:
        self._repo = repo
        self._vms = vms
        self._audit = audit
        self._cipher = cipher
        self._reader_factory = reader_factory

    async def list_connections(self, scope: AccessScope) -> list[tuple[Connection, int]]:
        """연결 목록과 연결별 활성 VM 수. 관리자 전용이다 (NFR-210)."""
        scope.require(Permission.CONNECTION_MANAGE)
        connections = await self._repo.list_all()
        counts = await self._vms.count_by_connection(scope)
        return [(c, counts.get(c.connection_id, 0)) for c in connections]

    async def create(
        self, scope: AccessScope, payload: ConnectionCreateInput, ip: str | None
    ) -> Connection:
        scope.require(Permission.CONNECTION_MANAGE)

        # FR-105 — DB UNIQUE 제약과 함께 이중 방어
        existing = await self._repo.find_by_target(payload.address, payload.username)
        if existing is not None:
            raise DuplicateError(
                "동일한 주소와 계정의 연결이 이미 등록되어 있습니다.",
                detail={
                    "existing_connection_id": str(existing.connection_id),
                    "existing_display_name": existing.display_name,
                },
            )

        conn = _to_connection(payload)
        conn.validate()  # 도메인 규칙 (계획 02 §10)

        created = await self._repo.insert(conn, self._cipher.encrypt(payload.password))
        await self._audit.record(
            AuditEvent(
                actor=scope.username,
                action=AuditAction.CONNECTION_CREATE,
                result="success",
                actor_ip=ip,
                target_type="connection",
                target_id=str(created.connection_id),
                # `exclude={"password"}`에 해당한다 — 애초에 비밀번호를 raw에 넣지 않는다.
                # `build_detail` 화이트리스트가 2차 방어다 (계획 10 §6.2).
                detail=build_detail(
                    AuditAction.CONNECTION_CREATE,
                    {
                        "display_name": payload.display_name,
                        "kind": payload.kind.value,
                        "address": payload.address,
                        "port": payload.port,
                    },
                ),
            )
        )
        return created

    async def delete(self, scope: AccessScope, connection_id: UUID, ip: str | None) -> None:
        scope.require(Permission.CONNECTION_MANAGE)
        conn = await self._repo.get(connection_id)
        if conn is None:
            raise NotFoundError("연결을 찾을 수 없습니다.")

        # `ON DELETE RESTRICT`가 이미 막지만, DB 에러를 500으로 흘리면 원인을 알 수 없다.
        # FR-109의 2단계 확인은 Step 3에서 구현한다 (ROADMAP §9.1).
        vm_count = await self._vms.count_active_vms(connection_id)
        if vm_count > 0:
            raise ValidationError(
                f"수집된 가상 머신 {vm_count}건이 있어 삭제할 수 없습니다. "
                "자원 보존 정책과 2단계 확인은 Step 3에서 제공합니다.",
                field="connection_id",
                detail={"vm_count": vm_count},
            )

        await self._repo.remove(connection_id)
        await self._audit.record(
            AuditEvent(
                actor=scope.username,
                action=AuditAction.CONNECTION_DELETE,
                result="success",
                actor_ip=ip,
                target_type="connection",
                target_id=str(connection_id),
                detail=build_detail(
                    AuditAction.CONNECTION_DELETE,
                    {"display_name": conn.display_name, "impact": {"virtual_machine": vm_count}},
                ),
            )
        )

    async def check_unsaved(
        self, scope: AccessScope, payload: ConnectionCreateInput, ip: str | None
    ) -> ConnectionCheckResult:
        """저장 전 연결 테스트 (FR-106). 자격증명을 저장하지 않고 임시 객체로만 쓴다."""
        scope.require(Permission.CONNECTION_MANAGE)
        conn = _to_connection(payload)
        conn.validate()
        return await self._run_check(scope, conn, ip)

    async def _run_check(
        self, scope: AccessScope, conn: Connection, ip: str | None
    ) -> ConnectionCheckResult:
        reader = self._reader_factory(conn)
        try:
            result = await reader.check_connection()
        finally:
            # 실패 경로에서도 세션을 해제한다
            await reader.close_session()

        await self._audit.record(
            AuditEvent(
                actor=scope.username,
                action=AuditAction.CONNECTION_TEST,
                result="success" if result.is_usable else "failure",
                actor_ip=ip,
                target_type="connection",
                target_id=str(conn.connection_id),
                detail=build_detail(
                    AuditAction.CONNECTION_TEST,
                    {
                        "is_usable": result.is_usable,
                        "failed_stage": result.failed_stage.value if result.failed_stage else None,
                    },
                ),
            )
        )
        return result
