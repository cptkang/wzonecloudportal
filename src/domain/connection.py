"""하이퍼바이저 연결 정보 (계획 02 §10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import SecretStr

from src.domain.enums import ConnectionKind, ConnectionStatus, WinRmAuth
from src.domain.exceptions import ValidationError


@dataclass
class Connection:
    #: 불변 (FR-110)
    connection_id: UUID
    kind: ConnectionKind
    display_name: str
    address: str
    port: int
    username: str
    #: SecretStr — repr에서 마스킹된다. 평문화는 어댑터 세션 생성 시점에만 한다 (NFR-209).
    password: SecretStr
    protocol: Literal["http", "https"] = "https"
    #: WinRM 인증 방식 (계획 05 §4.1). WinRM 연결에서 필수이며 vCenter는 항상 None.
    auth_method: WinRmAuth | None = None
    #: JEA 엔드포인트 이름 (계획 05 §4.3.1). hyperv-host/cluster 전용이며 vCenter·SCVMM은 None.
    session_configuration: str | None = None
    verify_tls: bool = True
    description: str | None = None
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __repr__(self) -> str:
        """자격증명이 로그에 노출되지 않도록 재정의한다 (NFR-203).

        dataclass 기본 `__repr__`은 모든 필드를 출력한다. `SecretStr`이 자체 마스킹을
        하더라도 명시적으로 제외하는 편이 안전하다.
        """
        return (
            f"Connection(id={self.connection_id}, kind={self.kind}, "
            f"name={self.display_name!r}, address={self.address}, status={self.status})"
        )

    @property
    def is_collectable(self) -> bool:
        """수집 대상 여부. 자격증명 오류 연결은 제외한다 (FR-114)."""
        return self.status is ConnectionStatus.ACTIVE

    def validate(self) -> None:
        """도메인 규칙 검증 (FR-104)."""
        if not (1 <= self.port <= 65535):
            raise ValidationError("포트는 1~65535 범위여야 합니다.", field="port")
        if self.kind is ConnectionKind.VCENTER and self.protocol != "https":
            raise ValidationError("vCenter 연결은 HTTPS만 지원합니다.", field="protocol")
        if self.kind.uses_winrm and self.auth_method is None:
            raise ValidationError(
                "WinRM 연결은 인증 방식(NTLM/Kerberos/CredSSP)이 필요합니다.", field="auth_method"
            )
        if not self.kind.uses_winrm and self.auth_method is not None:
            raise ValidationError(
                "vCenter 연결에는 WinRM 인증 방식을 지정할 수 없습니다.", field="auth_method"
            )
        # JEA 세션 구성은 경로 A 전용이다 (계획 05 §4.3.1). SCVMM은 기본 엔드포인트를 쓴다.
        if self.session_configuration is not None and self.kind not in (
            ConnectionKind.HYPERV_HOST,
            ConnectionKind.HYPERV_CLUSTER,
        ):
            raise ValidationError(
                "JEA 세션 구성은 Hyper-V 호스트/클러스터 연결에만 지정할 수 있습니다.",
                field="session_configuration",
            )
