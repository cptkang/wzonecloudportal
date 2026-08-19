"""연결 저장소 (계획 08 §5).

비밀번호는 암호문으로만 다룬다. 복호화는 어댑터 세션 생성 시점에만 한다 (NFR-209).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.connection import Connection
from src.domain.enums import ConnectionKind, ConnectionStatus, WinRmAuth
from src.infrastructure.db.models import ConnectionRow
from src.infrastructure.security.cipher import CredentialCipher


class ConnectionRepository:
    def __init__(self, session: AsyncSession, cipher: CredentialCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def list_all(self) -> list[Connection]:
        rows = (
            await self._session.execute(select(ConnectionRow).order_by(ConnectionRow.created_at))
        ).scalars()
        return [self._to_domain(row) for row in rows]

    async def get(self, connection_id: UUID) -> Connection | None:
        row = await self._session.get(ConnectionRow, connection_id)
        return self._to_domain(row) if row is not None else None

    async def find_by_target(self, address: str, username: str) -> Connection | None:
        row = (
            await self._session.execute(
                select(ConnectionRow).where(
                    ConnectionRow.address == address, ConnectionRow.username == username
                )
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row is not None else None

    async def list_names(self, connection_ids: Sequence[UUID] | None) -> list[tuple[UUID, str]]:
        """조회 화면의 연결 필터용 최소 정보 (id·이름). 자격증명·상태는 담지 않는다."""
        stmt = select(ConnectionRow.connection_id, ConnectionRow.display_name).order_by(
            ConnectionRow.display_name
        )
        if connection_ids is not None:
            stmt = stmt.where(ConnectionRow.connection_id.in_(list(connection_ids)))
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]

    async def insert(self, conn: Connection, encrypted_password: str) -> Connection:
        row = ConnectionRow(
            connection_id=conn.connection_id,
            kind=conn.kind.value,
            display_name=conn.display_name,
            address=conn.address,
            port=conn.port,
            username=conn.username,
            password_encrypted=encrypted_password,
            verify_tls=conn.verify_tls,
            protocol=conn.protocol,
            auth_method=conn.auth_method.value if conn.auth_method else None,
            session_configuration=conn.session_configuration,
            status=conn.status.value,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_domain(row)

    async def remove(self, connection_id: UUID) -> None:
        """포탈의 연결 레코드를 지운다. 하이퍼바이저 자원은 건드리지 않는다 (D-005).

        `ON DELETE RESTRICT` 때문에 수집된 VM이 있으면 실패한다.
        호출부가 미리 건수를 확인해 409로 변환한다 (ROADMAP §9.1).
        """
        row = await self._session.get(ConnectionRow, connection_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def mark_attempt(self, connection_id: UUID) -> None:
        """수집 시도를 기록한다. **이전 오류를 지운다.**

        지우지 않으면 UI 폴링이 이전 수집의 `last_error`를 보고 **시작하자마자
        실패로 판정한다** (`static/js/connections.js`의 `connState`). 실패는
        이번 시도의 결과로 `mark_failure`가 다시 채운다.
        """
        row = await self._session.get(ConnectionRow, connection_id)
        if row is not None:
            row.last_attempt_at = datetime.now(UTC)
            row.last_error = None
            await self._session.flush()

    async def mark_success(self, connection_id: UUID, at: datetime) -> None:
        row = await self._session.get(ConnectionRow, connection_id)
        if row is not None:
            row.last_success_at = at
            row.last_error = None
            row.status = ConnectionStatus.ACTIVE.value
            row.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def mark_failure(
        self, connection_id: UUID, error: str, status: ConnectionStatus
    ) -> None:
        """실패해도 **기존 수집 데이터는 삭제하지 않는다** (NFR-302).

        신선도만 오래된 채로 남고 UI가 이를 경고로 표시한다.
        """
        row = await self._session.get(ConnectionRow, connection_id)
        if row is not None:
            row.last_error = error
            row.status = status.value
            row.updated_at = datetime.now(UTC)
            await self._session.flush()

    def decrypt_password(self, stored: str) -> SecretStr:
        return self._cipher.decrypt(stored)

    async def load_with_password(self, connection_id: UUID) -> Connection | None:
        """복호화된 비밀번호를 담은 연결을 반환한다. 수집·연결 테스트 직전에만 호출한다."""
        row = await self._session.get(ConnectionRow, connection_id)
        if row is None:
            return None
        conn = self._to_domain(row)
        conn.password = self._cipher.decrypt(row.password_encrypted)
        return conn

    def _to_domain(self, row: ConnectionRow) -> Connection:
        """비밀번호는 빈 SecretStr로 둔다. 필요할 때만 `load_with_password`로 채운다."""
        return Connection(
            connection_id=row.connection_id,
            kind=ConnectionKind(row.kind),
            display_name=row.display_name,
            address=row.address,
            port=row.port,
            username=row.username,
            password=SecretStr(""),
            protocol="http" if row.protocol == "http" else "https",
            auth_method=WinRmAuth(row.auth_method) if row.auth_method else None,
            session_configuration=row.session_configuration,
            verify_tls=row.verify_tls,
            status=ConnectionStatus(row.status),
            last_success_at=row.last_success_at,
            last_attempt_at=row.last_attempt_at,
            last_error=row.last_error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
