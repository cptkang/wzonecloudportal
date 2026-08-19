"""가입 신청과 사용자 관리 (계획 09 §4.5·4.6).

가입은 **신청**이며 즉시 접근 권한이 되지 않는다 (D-014). 인벤토리 정보는 그 자체로
공격 표면 정보이므로(NFR-206) 누구나 가입해 즉시 조회할 수 있으면 읽기 전용으로 얻은
안전성이 무의미해진다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID

from pydantic import SecretStr

from src.domain.audit import AuditAction, AuditEvent
from src.domain.auth import (
    ALLOWED_TRANSITIONS,
    AccessScope,
    Permission,
    Role,
    UserStatus,
)
from src.domain.exceptions import NotFoundError, ValidationError
from src.infrastructure.repository.audit_repo import AuditRepository, build_detail
from src.infrastructure.repository.user_repo import ScopeRepository, UserRepository
from src.infrastructure.security.password import (
    generate_temporary_password,
    hash_password,
    validate_password_policy,
)

USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{3,64}$")
REGISTER_ACCEPTED_MESSAGE = "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."


class RegistrationService:
    def __init__(self, users: UserRepository, audit: AuditRepository) -> None:
        self._users = users
        self._audit = audit

    async def register(
        self,
        username: str,
        password: SecretStr,
        display_name: str | None,
        email: str | None,
        ip: str | None,
    ) -> None:
        """가입을 신청한다. **반환값이 없다** — 성공과 중복을 구분하지 않기 위함이다.

        중복 여부를 알려주면 가입 폼으로 계정 목록을 확인할 수 있다.
        """
        normalized = username.strip().lower()
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise ValidationError(
                "아이디는 3~64자의 영문 소문자·숫자·`.`·`_`·`-`만 사용할 수 있습니다.",
                field="username",
            )
        validate_password_policy(password)

        existing = await self._users.find_row_by_username(normalized)
        if existing is not None:
            # 중복이어도 같은 응답을 준다. 기록만 남긴다.
            await self._audit.record(
                AuditEvent(
                    actor=normalized,
                    action=AuditAction.USER_REGISTER_DUPLICATE,
                    result="failure",
                    actor_ip=ip,
                    target_type="user",
                    detail=build_detail(
                        AuditAction.USER_REGISTER_DUPLICATE, {"username": normalized}
                    ),
                )
            )
            return

        await self._users.add(
            username=normalized,
            password_hash=hash_password(password),
            display_name=display_name,
            email=email,
            role=Role.VIEWER,  # 승인 시 관리자가 다시 정한다
            status=UserStatus.PENDING,
        )
        await self._audit.record(
            AuditEvent(
                actor=normalized,
                action=AuditAction.USER_REGISTER,
                result="success",
                actor_ip=ip,
                target_type="user",
                detail=build_detail(AuditAction.USER_REGISTER, {"username": normalized}),
            )
        )


class UserAdminService:
    """모든 메서드가 admin 권한을 요구한다.

    요청 IP는 메서드마다 넘기지 않고 생성 시점에 받는다 — 이 서비스는 요청 단위로
    만들어지고, 7개 메서드 시그니처에 같은 인자를 반복하면 빠뜨리기 쉽다.
    """

    def __init__(
        self,
        users: UserRepository,
        scopes: ScopeRepository,
        audit: AuditRepository,
        actor_ip: str | None = None,
    ) -> None:
        self._users = users
        self._scopes = scopes
        self._audit = audit
        self._actor_ip = actor_ip

    async def approve(
        self, actor: AccessScope, user_id: UUID, role: Role, connection_ids: Sequence[UUID]
    ) -> None:
        """가입을 승인하면서 역할과 조회 범위를 함께 부여한다.

        범위를 비워 두면 아무것도 보이지 않는다 (기본 거부). 이는 정상 동작이다.
        """
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        _check_transition(UserStatus(row.status), UserStatus.ACTIVE)

        await self._users.set_status(user_id, UserStatus.ACTIVE, approved_by=actor.username)
        await self._users.set_role(user_id, role)
        # admin은 범위 테이블과 무관하게 전체를 본다
        ids = () if role is Role.ADMIN else connection_ids
        await self._scopes.replace(user_id, ids, granted_by=actor.username)
        await self._record(
            actor,
            AuditAction.USER_APPROVE,
            user_id,
            {"username": row.username, "role": role.value, "connection_count": len(ids)},
        )

    async def reject(self, actor: AccessScope, user_id: UUID, reason: str | None) -> None:
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        _check_transition(UserStatus(row.status), UserStatus.REJECTED)
        await self._users.set_status(user_id, UserStatus.REJECTED, reject_reason=reason)
        await self._record(
            actor,
            AuditAction.USER_REJECT,
            user_id,
            {"username": row.username, "reason": reason},
        )

    async def disable(self, actor: AccessScope, user_id: UUID) -> None:
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        self._guard_self(actor, user_id, "자기 자신의 계정 상태는 변경할 수 없습니다.")
        _check_transition(UserStatus(row.status), UserStatus.DISABLED)
        if Role(row.role) is Role.ADMIN:
            await self._guard_last_admin(user_id)
        await self._users.set_status(user_id, UserStatus.DISABLED)
        await self._record(
            actor, AuditAction.USER_DISABLE, user_id, {"username": row.username}
        )

    async def enable(self, actor: AccessScope, user_id: UUID) -> None:
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        _check_transition(UserStatus(row.status), UserStatus.ACTIVE)
        await self._users.set_status(user_id, UserStatus.ACTIVE, approved_by=actor.username)
        await self._record(
            actor, AuditAction.USER_ENABLE, user_id, {"username": row.username}
        )

    async def change_role(self, actor: AccessScope, user_id: UUID, role: Role) -> None:
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        self._guard_self(actor, user_id, "자기 자신의 역할은 변경할 수 없습니다.")
        if Role(row.role) is Role.ADMIN and role is not Role.ADMIN:
            await self._guard_last_admin(user_id)
        await self._users.set_role(user_id, role)
        if role is Role.ADMIN:
            # 관리자는 범위 제한을 받지 않으므로 남은 범위 행을 정리한다
            await self._scopes.replace(user_id, (), granted_by=actor.username)
        await self._record(
            actor,
            AuditAction.USER_ROLE_CHANGE,
            user_id,
            {"username": row.username, "role": role.value},
        )

    async def set_scopes(
        self, actor: AccessScope, user_id: UUID, connection_ids: Sequence[UUID]
    ) -> None:
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        if Role(row.role) is Role.ADMIN:
            raise ValidationError("관리자는 범위 제한을 받지 않습니다.", field="role")
        await self._scopes.replace(user_id, connection_ids, granted_by=actor.username)
        await self._record(
            actor,
            AuditAction.SCOPE_UPDATE,
            user_id,
            {"username": row.username, "connection_count": len(connection_ids)},
        )

    async def reset_password(self, actor: AccessScope, user_id: UUID) -> SecretStr:
        """임시 비밀번호를 생성해 반환한다.

        반환값은 화면에 1회만 표시하며 **감사 로그·DB에 평문을 남기지 않는다** (FR-1008).
        """
        actor.require(Permission.USER_MANAGE)
        row = await self._require_user(user_id)
        temporary = generate_temporary_password()
        await self._users.update_hash(user_id, hash_password(temporary), must_change=True)
        await self._record(
            actor,
            AuditAction.USER_PASSWORD_RESET,
            user_id,
            {"username": row.username},  # 값이 아니라 "발급됨" 사실만
        )
        return temporary

    async def _require_user(self, user_id: UUID):  # type: ignore[no-untyped-def]
        row = await self._users.get_row(user_id)
        if row is None:
            raise NotFoundError("사용자를 찾을 수 없습니다.")
        return row

    def _guard_self(self, actor: AccessScope, user_id: UUID, message: str) -> None:
        """실수로 스스로를 잠그는 것이 가장 흔한 사고다."""
        if actor.user_id == user_id:
            raise ValidationError(message, field="user_id")

    async def _guard_last_admin(self, user_id: UUID) -> None:
        """마지막 활성 관리자의 강등·비활성화를 막는다.

        막지 않으면 관리자가 0명이 되어 아무도 승인·연결 관리를 할 수 없는 잠금 상태가
        되고, DB를 직접 고치는 수밖에 없다 (계획 09 §4.6.1).
        """
        if await self._users.count_active_admins(exclude=user_id) == 0:
            raise ValidationError("마지막 관리자입니다. 다른 관리자를 지정한 뒤에 변경하세요.")

    async def _record(
        self,
        actor: AccessScope,
        action: AuditAction,
        user_id: UUID,
        raw: dict[str, object],
    ) -> None:
        await self._audit.record(
            AuditEvent(
                actor=actor.username,
                action=action,
                result="success",
                actor_ip=self._actor_ip,
                target_type="user",
                target_id=str(user_id),
                detail=build_detail(action, raw),
            )
        )


def _check_transition(current: UserStatus, target: UserStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValidationError(
            f"{current.value} → {target.value} 상태 전이는 허용되지 않습니다.", field="status"
        )
