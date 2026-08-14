"""로그인·비밀번호 변경 (계획 09 §4).

**응답 메시지를 통일한다.** "계정 없음"과 "비밀번호 불일치"를 구분하면 계정 열거가
가능해진다. 상태 사유는 **비밀번호 검증을 통과한 뒤에만** 노출한다 — 순서를 바꾸면
비밀번호를 모르는 사람도 계정 존재를 알아낼 수 있다 (D-014 §4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import SecretStr

from src.config import Settings
from src.domain.audit import AuditAction, AuditEvent
from src.domain.auth import LOGIN_ALLOWED, STATUS_MESSAGE, AuthenticatedUser, UserStatus
from src.domain.exceptions import AuthenticationError, ValidationError
from src.infrastructure.repository.audit_repo import AuditRepository, build_detail
from src.infrastructure.repository.user_repo import UserRepository, to_authenticated
from src.infrastructure.security.password import (
    fake_verify,
    hash_password,
    validate_password_policy,
    verify_password,
)
from src.infrastructure.security.tokens import create_access_token

GENERIC_FAILURE = "아이디 또는 비밀번호가 올바르지 않습니다."


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        audit: AuditRepository,
        settings: Settings,
    ) -> None:
        self._users = users
        self._audit = audit
        self._settings = settings

    async def login(self, username: str, password: SecretStr, ip: str | None) -> tuple[str, AuthenticatedUser]:
        normalized = username.strip().lower()
        row = await self._users.find_row_by_username(normalized)

        # 계정 열거 방지: 계정 없음과 비밀번호 불일치를 구분하지 않는다.
        if row is None:
            fake_verify()  # 타이밍 차이 완화
            await self._audit_failure(normalized, ip, "invalid_credentials")
            raise AuthenticationError(GENERIC_FAILURE)

        if row.locked_until is not None and row.locked_until > datetime.now(UTC):
            await self._audit_failure(normalized, ip, "locked")
            raise AuthenticationError("계정이 일시적으로 잠겨 있습니다. 잠시 후 다시 시도하세요.")

        ok, new_hash = verify_password(password, row.password_hash)
        if not ok:
            await self._users.register_failure(
                row.user_id,
                max_attempts=self._settings.max_failed_login_attempts,
                lockout_minutes=self._settings.lockout_minutes,
            )
            await self._audit_failure(normalized, ip, "invalid_credentials")
            raise AuthenticationError(GENERIC_FAILURE)

        # 비밀번호가 맞은 뒤에야 상태별 사유를 알려준다.
        status = UserStatus(row.status)
        if status not in LOGIN_ALLOWED:
            await self._audit_failure(normalized, ip, f"status_{status.value}")
            raise AuthenticationError(STATUS_MESSAGE[status])

        if new_hash is not None:
            await self._users.update_hash(row.user_id, new_hash, must_change=row.must_change_password)
        await self._users.reset_failures(row.user_id)

        user = to_authenticated(row)
        await self._audit.record(
            AuditEvent(
                actor=user.username,
                action=AuditAction.LOGIN,
                result="success",
                actor_ip=ip,
                target_type="user",
                target_id=str(user.user_id),
                detail=build_detail(AuditAction.LOGIN, {"username": user.username}),
            )
        )
        return create_access_token(user, self._settings), user

    async def change_password(
        self, user: AuthenticatedUser, current: SecretStr, new: SecretStr, ip: str | None
    ) -> None:
        """본인 비밀번호 변경. **현재 비밀번호 확인을 요구한다** (FR-1008)."""
        row = await self._users.get_row(user.user_id)
        if row is None:
            raise AuthenticationError("세션이 유효하지 않습니다. 다시 로그인하세요.")

        ok, _ = verify_password(current, row.password_hash)
        if not ok:
            raise ValidationError("현재 비밀번호가 올바르지 않습니다.", field="current_password")

        validate_password_policy(new)
        if new.get_secret_value() == current.get_secret_value():
            raise ValidationError("이전과 다른 비밀번호를 사용하세요.", field="new_password")

        await self._users.update_hash(user.user_id, hash_password(new), must_change=False)
        await self._audit.record(
            AuditEvent(
                actor=user.username,
                action=AuditAction.PASSWORD_CHANGE,
                result="success",
                actor_ip=ip,
                target_type="user",
                target_id=str(user.user_id),
                detail=build_detail(AuditAction.PASSWORD_CHANGE, {"username": user.username}),
            )
        )

    async def record_logout(self, user: AuthenticatedUser, ip: str | None) -> None:
        await self._audit.record(
            AuditEvent(
                actor=user.username,
                action=AuditAction.LOGOUT,
                result="success",
                actor_ip=ip,
                target_type="user",
                target_id=str(user.user_id),
                detail=build_detail(AuditAction.LOGOUT, {"username": user.username}),
            )
        )

    async def _audit_failure(self, username: str, ip: str | None, reason: str) -> None:
        await self._audit.record(
            AuditEvent(
                actor=username,
                action=AuditAction.LOGIN,
                result="failure",
                actor_ip=ip,
                target_type="user",
                detail=build_detail(AuditAction.LOGIN, {"username": username, "reason": reason}),
            )
        )
