"""Hyper-V/SCVMM 연결 지원 (계획 05, D-012)

connections에 WinRM 관련 컬럼 3개를 더하고, kind의 DEFAULT 'vcenter'를 제거한다
(ROADMAP §7 — "Step 5에서 hyperv 추가 + DEFAULT 제거"). 값 검증 CHECK 3종을 추가한다.

Revision ID: 0002_hyperv_connections
Revises: 0001_step1_initial
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_hyperv_connections"
down_revision: str | None = "0001_step1_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("protocol", sa.Text(), nullable=False, server_default="https"),
    )
    op.add_column("connections", sa.Column("auth_method", sa.Text(), nullable=True))
    op.add_column("connections", sa.Column("session_configuration", sa.Text(), nullable=True))

    # 기존 행은 전부 vcenter라 데이터 보정이 필요 없다. DEFAULT만 제거한다.
    op.alter_column("connections", "kind", server_default=None)

    op.create_check_constraint(
        "ck_connections_kind",
        "connections",
        "kind IN ('vcenter', 'hyperv-host', 'hyperv-cluster', 'scvmm')",
    )
    op.create_check_constraint(
        "ck_connections_protocol", "connections", "protocol IN ('http', 'https')"
    )
    op.create_check_constraint(
        "ck_connections_auth_method",
        "connections",
        "auth_method IS NULL OR auth_method IN ('ntlm', 'kerberos', 'credssp')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_connections_auth_method", "connections", type_="check")
    op.drop_constraint("ck_connections_protocol", "connections", type_="check")
    op.drop_constraint("ck_connections_kind", "connections", type_="check")
    op.alter_column("connections", "kind", server_default="vcenter")
    op.drop_column("connections", "session_configuration")
    op.drop_column("connections", "auth_method")
    op.drop_column("connections", "protocol")
