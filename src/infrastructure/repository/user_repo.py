"""사용자·조회 범위 저장소 (계획 09 §5).

**계정을 물리 삭제하는 메서드가 없다.** 감사 로그의 행위자 참조가 끊기면
"누가 이 연결을 등록했는가"를 추적할 수 없다 (D-014).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.auth import AuthenticatedUser, Role, UserStatus
from src.infrastructure.db.models import UserConnectionScopeRow, UserRow


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self) -> int:
        return int(await self._session.scalar(select(func.count()).select_from(UserRow)) or 0)

    async def count_active_admins(self, exclude: UUID | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(UserRow)
            .where(UserRow.role == Role.ADMIN.value, UserRow.status == UserStatus.ACTIVE.value)
        )
        if exclude is not None:
            stmt = stmt.where(UserRow.user_id != exclude)
        return int(await self._session.scalar(stmt) or 0)

    async def find_row_by_username(self, username: str) -> UserRow | None:
        return (
            await self._session.execute(select(UserRow).where(UserRow.username == username))
        ).scalar_one_or_none()

    async def get_row(self, user_id: UUID) -> UserRow | None:
        return await self._session.get(UserRow, user_id)

    async def get(self, user_id: UUID) -> AuthenticatedUser | None:
        row = await self.get_row(user_id)
        return to_authenticated(row) if row is not None else None

    async def list_rows(self, status: UserStatus | None = None) -> list[UserRow]:
        stmt = select(UserRow).order_by(UserRow.created_at)
        if status is not None:
            stmt = stmt.where(UserRow.status == status.value)
        return list((await self._session.execute(stmt)).scalars())

    async def add(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        email: str | None = None,
        role: Role = Role.VIEWER,
        status: UserStatus = UserStatus.PENDING,
    ) -> UserRow:
        row = UserRow(
            user_id=uuid4(),
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            email=email,
            role=role.value,
            status=status.value,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_hash(self, user_id: UUID, password_hash: str, *, must_change: bool) -> None:
        row = await self.get_row(user_id)
        if row is not None:
            row.password_hash = password_hash
            row.must_change_password = must_change
            row.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def register_failure(
        self, user_id: UUID, *, max_attempts: int, lockout_minutes: int
    ) -> None:
        from datetime import timedelta

        row = await self.get_row(user_id)
        if row is None:
            return
        row.failed_login_count += 1
        if row.failed_login_count >= max_attempts:
            row.locked_until = datetime.now(UTC) + timedelta(minutes=lockout_minutes)
            row.failed_login_count = 0
        await self._session.flush()

    async def reset_failures(self, user_id: UUID) -> None:
        row = await self.get_row(user_id)
        if row is not None:
            row.failed_login_count = 0
            row.locked_until = None
            row.last_login_at = datetime.now(UTC)
            await self._session.flush()

    async def set_status(
        self,
        user_id: UUID,
        status: UserStatus,
        *,
        approved_by: str | None = None,
        reject_reason: str | None = None,
    ) -> None:
        row = await self.get_row(user_id)
        if row is None:
            return
        row.status = status.value
        row.updated_at = datetime.now(UTC)
        if status is UserStatus.ACTIVE and approved_by is not None:
            row.approved_by = approved_by
            row.approved_at = datetime.now(UTC)
        if status is UserStatus.REJECTED:
            row.reject_reason = reject_reason
        await self._session.flush()

    async def set_role(self, user_id: UUID, role: Role) -> None:
        row = await self.get_row(user_id)
        if row is not None:
            row.role = role.value
            row.updated_at = datetime.now(UTC)
            await self._session.flush()


class ScopeRepository:
    """조회 범위. 행이 없는 viewer/operator는 아무것도 볼 수 없다 (기본 거부)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> list[UUID]:
        rows = await self._session.execute(
            select(UserConnectionScopeRow.connection_id).where(
                UserConnectionScopeRow.user_id == user_id
            )
        )
        return [row[0] for row in rows.all()]

    async def replace(
        self, user_id: UUID, connection_ids: Sequence[UUID], granted_by: str | None
    ) -> None:
        """전체 교체(멱등). 부분 추가 API를 만들면 회수 누락이 생긴다."""
        await self._session.execute(
            delete(UserConnectionScopeRow).where(UserConnectionScopeRow.user_id == user_id)
        )
        for connection_id in dict.fromkeys(connection_ids):
            self._session.add(
                UserConnectionScopeRow(
                    user_id=user_id, connection_id=connection_id, granted_by=granted_by
                )
            )
        await self._session.flush()

    async def counts_by_user(self, user_ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        if not user_ids:
            return {}
        rows = await self._session.execute(
            select(UserConnectionScopeRow.user_id, UserConnectionScopeRow.connection_id).where(
                UserConnectionScopeRow.user_id.in_(list(user_ids))
            )
        )
        out: dict[UUID, list[UUID]] = {uid: [] for uid in user_ids}
        for user_id, connection_id in rows.all():
            out[user_id].append(connection_id)
        return out


def to_authenticated(row: UserRow) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=row.user_id,
        username=row.username,
        display_name=row.display_name,
        role=Role(row.role),
        status=UserStatus(row.status),
        must_change_password=row.must_change_password,
    )
