"""사용자·역할·조회 범위 (계획 09 §2·§3, D-014).

조회 범위 제한이 이 프로젝트의 핵심 보안 통제다 — 인벤토리 정보(IP·호스트명·OS)는
그 자체로 공격 표면 정보이기 때문이다 (NFR-206).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from src.domain.exceptions import PermissionError as DomainPermissionError


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(StrEnum):
    RESOURCE_READ = "resource.read"
    METADATA_WRITE = "metadata.write"
    CONNECTION_MANAGE = "connection.manage"
    COLLECTION_TRIGGER = "collection.trigger"
    USER_MANAGE = "user.manage"
    EXPORT = "export"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.RESOURCE_READ, Permission.EXPORT}),
    Role.OPERATOR: frozenset(
        {Permission.RESOURCE_READ, Permission.METADATA_WRITE, Permission.EXPORT}
    ),
    Role.ADMIN: frozenset(Permission),
}


class UserStatus(StrEnum):
    """계정 생애주기 (D-014).

    `is_active` 불리언으로는 '승인 대기'와 '비활성화'를 구분할 수 없다.
    """

    #: 가입 신청됨 — 로그인 불가
    PENDING = "pending"
    ACTIVE = "active"
    #: 관리자가 비활성화 — 로그인 불가
    DISABLED = "disabled"
    #: 가입 거부됨 — 종료 상태
    REJECTED = "rejected"


#: 로그인이 허용되는 상태는 하나뿐이다. 새 상태를 추가할 때 이 집합을 반드시 검토한다.
LOGIN_ALLOWED: frozenset[UserStatus] = frozenset({UserStatus.ACTIVE})

ALLOWED_TRANSITIONS: dict[UserStatus, frozenset[UserStatus]] = {
    UserStatus.PENDING: frozenset({UserStatus.ACTIVE, UserStatus.REJECTED}),
    UserStatus.ACTIVE: frozenset({UserStatus.DISABLED}),
    UserStatus.DISABLED: frozenset({UserStatus.ACTIVE}),
    #: 종료 상태 — 재신청은 새 계정으로 한다
    UserStatus.REJECTED: frozenset(),
}

STATUS_MESSAGE: dict[UserStatus, str] = {
    UserStatus.PENDING: "가입 신청이 검토 중입니다. 관리자 승인 후 로그인할 수 있습니다.",
    UserStatus.REJECTED: "가입이 승인되지 않았습니다. 관리자에게 문의하세요.",
    UserStatus.DISABLED: "비활성화된 계정입니다. 관리자에게 문의하세요.",
}


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: UUID
    username: str
    display_name: str | None
    role: Role
    status: UserStatus
    #: 관리자가 임시 비밀번호를 발급한 경우 (FR-1008)
    must_change_password: bool = False

    def has(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]

    @property
    def can_sign_in(self) -> bool:
        return self.status in LOGIN_ALLOWED


@dataclass(frozen=True, slots=True)
class AccessScope:
    """사용자의 조회 범위. 모든 조회 쿼리에 적용된다 (FR-1003)."""

    user_id: UUID
    username: str
    role: Role
    #: None = 전체 (admin만). 빈 frozenset = 아무것도 못 봄 (기본 거부).
    allowed_connection_ids: frozenset[UUID] | None

    @property
    def is_unrestricted(self) -> bool:
        return self.allowed_connection_ids is None

    @property
    def is_empty(self) -> bool:
        """범위가 비어 있으면 아무것도 볼 수 없다 (기본 거부)."""
        return self.allowed_connection_ids is not None and not self.allowed_connection_ids

    def can_access(self, connection_id: UUID) -> bool:
        return self.is_unrestricted or connection_id in (self.allowed_connection_ids or frozenset())

    def require(self, permission: Permission) -> None:
        """권한을 검사한다. API 라우터와 유스케이스 양쪽에서 호출한다 (이중 방어)."""
        if permission not in ROLE_PERMISSIONS[self.role]:
            raise DomainPermissionError(f"권한이 없습니다: {permission.value}")


def build_scope(user: AuthenticatedUser, connection_ids: Sequence[UUID]) -> AccessScope:
    return AccessScope(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        allowed_connection_ids=None if user.role is Role.ADMIN else frozenset(connection_ids),
    )
