"""SQLAlchemy ORM 모델 (ROADMAP §7 DDL).

**SQLAlchemy 모델을 도메인 엔티티로 겸용하지 않는다.** 매핑 코드가 늘더라도 계층을
지킨다 (계획 06 §13).

Step 1의 테이블은 6개다 — connections, virtual_machines, resource_identities,
users, user_connection_scopes, audit_events. 이 목록 밖의 테이블이 생기면 범위 이탈이다
(ROADMAP §7.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConnectionRow(Base):
    __tablename__ = "connections"

    connection_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    #: hyperv 값이 추가되어 DEFAULT를 제거했다 (ROADMAP §7, 마이그레이션 0002)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="443")
    username: Mapped[str] = mapped_column(Text, nullable=False)
    #: {key_version}${nonce}${ciphertext} (계획 10 §3.1)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: WinRM은 http(5985)/https(5986) 선택이 있다. vCenter는 항상 https (계획 05 §4)
    protocol: Mapped[str] = mapped_column(Text, nullable=False, server_default="https")
    #: WinRM 인증 방식 (계획 05 §4.1). vCenter는 NULL
    auth_method: Mapped[str | None] = mapped_column(Text)
    #: JEA 세션 구성 이름 (계획 05 §4.3.1). hyperv-host/cluster 전용
    session_configuration: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # FR-105를 DB 레벨에서 강제한다. 애플리케이션 검증만으로는 동시 요청에서 뚫린다.
        UniqueConstraint("address", "username", name="uq_connection_target"),
        CheckConstraint(
            "kind IN ('vcenter', 'hyperv-host', 'hyperv-cluster', 'scvmm')",
            name="ck_connections_kind",
        ),
        CheckConstraint("protocol IN ('http', 'https')", name="ck_connections_protocol"),
        CheckConstraint(
            "auth_method IS NULL OR auth_method IN ('ntlm', 'kerberos', 'credssp')",
            name="ck_connections_auth_method",
        ),
    )


class VirtualMachineRow(Base):
    __tablename__ = "virtual_machines"

    resource_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    connection_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("connections.connection_id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: config.instanceUuid
    native_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: config.uuid — Step 5의 교차 식별 대비해 지금부터 저장한다
    bios_uuid: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    power_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    connection_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    vcpu_count: Mapped[int | None] = mapped_column(Integer)
    memory_mb: Mapped[int | None] = mapped_column(BigInteger)
    configured_os: Mapped[str | None] = mapped_column(Text)
    #: FR-501 3분법
    guest_availability: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    guest_os_name: Mapped[str | None] = mapped_column(Text)
    #: guest_tools | vm_config (FR-304)
    guest_os_source: Mapped[str | None] = mapped_column(Text)
    guest_hostname: Mapped[str | None] = mapped_column(Text)
    #: 게스트 값이 실제 수집된 시각. 폴백 시 이전 시각을 유지한다 (ROADMAP §7.4)
    guest_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    host_native_id: Mapped[str | None] = mapped_column(Text)
    #: 값은 항상 'active'지만 부분 인덱스 조건에 들어가므로 지금 만든다 (ROADMAP §7)
    lifecycle: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # CI 식별 1순위 (FR-303). 로직에 버그가 있어도 중복이 물리적으로 생기지 않게 하는 최후 방어.
    __table_args__ = (UniqueConstraint("connection_id", "native_id", name="uq_vm_native"),)


# 조회의 대부분이 활성 자원 대상이므로 부분 인덱스로 크기를 줄인다 (계획 06 §2.9).
Index(
    "idx_vm_conn_active",
    VirtualMachineRow.connection_id,
    postgresql_where=VirtualMachineRow.lifecycle == "active",
)


class ResourceIdentityRow(Base):
    """식별 키 인덱스 (계획 06 §2.7). Step 1은 rule=1만 기록한다."""

    __tablename__ = "resource_identities"

    resource_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    connection_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: 1(native) | 2(bios_uuid) | 3(mac+name)
    rule: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    key_value: Mapped[str] = mapped_column(Text, primary_key=True)


Index("idx_identities_lookup", ResourceIdentityRow.rule, ResourceIdentityRow.key_value)
Index("idx_identities_resource", ResourceIdentityRow.resource_id)


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    #: 소문자 정규화하여 저장한다
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="viewer")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'disabled', 'rejected')", name="ck_users_status"
        ),
        CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="ck_users_role"),
    )


# 관리자 화면의 첫 화면이 '승인 대기 목록'이므로 부분 인덱스를 둔다 (계획 09 §5).
Index("idx_users_pending", UserRow.created_at, postgresql_where=UserRow.status == "pending")


class UserConnectionScopeRow(Base):
    """행이 없는 viewer/operator는 아무것도 볼 수 없다 (기본 거부, 계획 09 §5)."""

    __tablename__ = "user_connection_scopes"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("connections.connection_id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_by: Mapped[str | None] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index("idx_scopes_user", UserConnectionScopeRow.user_id)


class AuditEventRow(Base):
    """조회 화면은 Step 8이지만 기록은 지금부터 — 이력은 소급되지 않는다."""

    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: username. 계정은 삭제되지 않으므로 항상 추적 가능하다 (D-014)
    actor: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    client_ip: Mapped[str | None] = mapped_column(INET)
    #: 화이트리스트 키만 담는다 (계획 10 §6.2)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


Index("idx_audit_occurred", AuditEventRow.occurred_at.desc())
Index("idx_audit_actor", AuditEventRow.actor, AuditEventRow.occurred_at.desc())
