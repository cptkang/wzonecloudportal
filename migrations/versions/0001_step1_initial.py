"""Step 1 초기 스키마 (ROADMAP §7)

테이블 6개 + pg_trgm 확장.
마이그레이션 순서는 connections → users → scopes다 (scopes가 둘을 참조한다).

Revision ID: 0001_step1_initial
Revises:
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_step1_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 실제 사용은 Step 4(통합 검색)지만 권한 확인을 앞당기려 지금 만든다 (ROADMAP §4.1).
    # 확장 생성은 상위 DB 권한이 필요하고, 승인 대기는 구현보다 오래 걸린다.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "connections",
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="vcenter"),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # FR-105 — 애플리케이션 검증만으로는 동시 요청에서 뚫린다
        sa.UniqueConstraint("address", "username", name="uq_connection_target"),
    )

    op.create_table(
        "virtual_machines",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.connection_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column("bios_uuid", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("power_state", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("connection_state", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("vcpu_count", sa.Integer()),
        sa.Column("memory_mb", sa.BigInteger()),
        sa.Column("configured_os", sa.Text()),
        sa.Column("guest_availability", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("guest_os_name", sa.Text()),
        sa.Column("guest_os_source", sa.Text()),
        sa.Column("guest_hostname", sa.Text()),
        sa.Column("guest_observed_at", sa.DateTime(timezone=True)),
        sa.Column("host_native_id", sa.Text()),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        # CI 식별 1순위 (FR-303)
        sa.UniqueConstraint("connection_id", "native_id", name="uq_vm_native"),
    )
    op.create_index(
        "idx_vm_conn_active",
        "virtual_machines",
        ["connection_id"],
        postgresql_where=sa.text("lifecycle = 'active'"),
    )

    # 계획 06 §2.7. Step 1은 rule=1만 기록하지만 2·3순위 추가 시 구조가 바뀌지 않는다.
    op.create_table(
        "resource_identities",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule", sa.SmallInteger(), nullable=False),
        sa.Column("key_value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("rule", "key_value", "resource_id"),
    )
    op.create_index("idx_identities_lookup", "resource_identities", ["rule", "key_value"])
    op.create_index("idx_identities_resource", "resource_identities", ["resource_id"])

    # ── 계정 (D-014) ──────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'disabled', 'rejected')", name="ck_users_status"
        ),
        sa.CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="ck_users_role"),
    )
    op.create_index(
        "idx_users_pending", "users", ["created_at"], postgresql_where=sa.text("status = 'pending'")
    )

    op.create_table(
        "user_connection_scopes",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.connection_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("granted_by", sa.Text()),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id", "connection_id"),
    )
    op.create_index("idx_scopes_user", "user_connection_scopes", ["user_id"])

    # 감사 기록 (FR-1004). 조회 화면은 Step 8이지만 이력은 소급되지 않으므로 지금부터 쌓는다.
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("client_ip", postgresql.INET()),
        sa.Column("detail", postgresql.JSONB()),
    )
    op.create_index("idx_audit_occurred", "audit_events", [sa.text("occurred_at DESC")])
    op.create_index("idx_audit_actor", "audit_events", ["actor", sa.text("occurred_at DESC")])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("user_connection_scopes")
    op.drop_table("users")
    op.drop_table("resource_identities")
    op.drop_table("virtual_machines")
    op.drop_table("connections")
    # pg_trgm은 다른 스키마가 쓸 수 있으므로 내리지 않는다.
