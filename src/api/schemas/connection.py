"""연결 요청·응답 스키마 (계획 08 §5.1·5.2).

`kind`를 판별자로 하는 discriminated union이다 (D-012, 계획 05 §2):
vcenter / hyperv-host·hyperv-cluster(경로 A) / scvmm(경로 B).
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from src.domain.connection import Connection
from src.domain.enums import (
    CheckStage,
    ConnectionKind,
    ConnectionStatus,
    ResourceType,
    WinRmAuth,
)
from src.domain.ports import ConnectionCheckResult

_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


class _ConnectionCreateBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1)
    verify_tls: bool = True

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        v = v.strip()
        if not (_is_valid_hostname(v) or _is_valid_ip(v)):
            raise ValueError("올바른 FQDN 또는 IP 주소가 아닙니다.")
        return v


class VCenterConnectionCreate(_ConnectionCreateBase):
    kind: Literal[ConnectionKind.VCENTER] = ConnectionKind.VCENTER
    port: int = Field(default=443, ge=1, le=65535)


class _WinRmConnectionCreate(_ConnectionCreateBase):
    """WinRM 공통 필드 (계획 05 §4). HTTP 5985 / HTTPS 5986, 기본은 HTTPS."""

    port: int = Field(default=5986, ge=1, le=65535)
    protocol: Literal["http", "https"] = "https"
    auth_method: WinRmAuth


class HyperVHostConnectionCreate(_WinRmConnectionCreate):
    """경로 A — 호스트 1대 또는 클러스터 1개 = 연결 1개 (계획 05 §2).

    SCVMM이 관리하는 호스트는 등록하지 않는다 — 같은 VM이 두 자원으로 중복 생성된다 (§2.1).
    """

    kind: Literal[ConnectionKind.HYPERV_HOST, ConnectionKind.HYPERV_CLUSTER]
    #: JEA 세션 구성 이름 (계획 05 §4.3.1). 미지정 시 기본 엔드포인트 — 개발·검증 전용이며,
    #: 운영 호스트는 JEA 구성 전에는 등록하지 않는다 (CLAUDE.md Key Constraints).
    session_configuration: str | None = Field(default=None, max_length=100)


class ScvmmConnectionCreate(_WinRmConnectionCreate):
    """경로 B — SCVMM 관리 서버 자신에 접속한다. 콘솔 설치 서버 경유는 이중 홉이다 (§4.2)."""

    kind: Literal[ConnectionKind.SCVMM] = ConnectionKind.SCVMM


ConnectionCreate = Annotated[
    VCenterConnectionCreate | HyperVHostConnectionCreate | ScvmmConnectionCreate,
    Field(discriminator="kind"),
]


class ConnectionResponse(BaseModel):
    """**`password` 필드가 스키마에 아예 없다.** 있으면 언젠가 채워진다 (계획 08 §5.2)."""

    connection_id: UUID
    kind: ConnectionKind
    display_name: str
    address: str
    port: int
    username: str
    #: 값이 아니라 존재 여부만 (NFR-203)
    has_password: bool = True
    verify_tls: bool
    status: ConnectionStatus
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    vm_count: int = 0

    @classmethod
    def from_domain(cls, conn: Connection, vm_count: int = 0) -> "ConnectionResponse":
        return cls(
            connection_id=conn.connection_id,
            kind=conn.kind,
            display_name=conn.display_name,
            address=conn.address,
            port=conn.port,
            username=conn.username,
            verify_tls=conn.verify_tls,
            status=conn.status,
            last_success_at=conn.last_success_at,
            last_attempt_at=conn.last_attempt_at,
            last_error=conn.last_error,
            vm_count=vm_count,
        )


class StageResponse(BaseModel):
    stage: CheckStage
    passed: bool
    skipped: bool
    detail: str | None
    elapsed_ms: int | None


class ConnectionTestResponse(BaseModel):
    """**HTTP 200으로 반환한다.** 테스트 자체는 성공했고 결과가 실패인 것이다.

    연결 실패를 500·502로 반환하면 클라이언트가 "요청 처리 실패"와 "연결 실패"를
    구분할 수 없다 (계획 08 §5.4).
    """

    is_usable: bool
    stages: list[StageResponse]
    readable_types: list[ResourceType]
    server_version: str | None
    failed_stage: CheckStage | None

    @classmethod
    def from_domain(cls, result: ConnectionCheckResult) -> "ConnectionTestResponse":
        return cls(
            is_usable=result.is_usable,
            stages=[
                StageResponse(
                    stage=s.stage,
                    passed=s.passed,
                    skipped=s.skipped,
                    detail=s.detail,
                    elapsed_ms=s.elapsed_ms,
                )
                for s in result.stages
            ],
            readable_types=sorted(result.readable_types),
            server_version=result.server_version,
            failed_stage=result.failed_stage,
        )


def _is_valid_hostname(value: str) -> bool:
    return bool(_HOSTNAME_PATTERN.fullmatch(value))


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
