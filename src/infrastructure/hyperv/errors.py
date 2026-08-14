"""pypsrp/WinRM 예외 → 도메인 예외 (계획 05 §11).

**pypsrp 예외가 어댑터 밖으로 나가면 안 된다.** 메시지에 접속 정보가 섞일 수 있고,
상위 계층이 pypsrp 타입에 결합된다.

pypsrp 0.9는 인증 실패를 `pypsrp.exceptions.AuthenticationError` 타입으로 던진다
(2026-08-14 라이브러리 소스로 확인 — 계획 05 §11의 [검증 필요] 해소).
문자열 매칭은 타입으로 못 잡는 전송 계층 실패의 보조 수단으로만 남긴다.

**모호하면 재시도하지 않는 쪽(AuthenticationError)으로 판정한다.**
계정 잠금이 재시도 지연보다 훨씬 큰 피해다 (CST-05).
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Iterable

from pypsrp.exceptions import (
    AuthenticationError as PypsrpAuthenticationError,
    WinRMError,
    WinRMTransportError,
    WSManFaultError,
)

from src.domain.exceptions import (
    AuthenticationError,
    CollectionError,
    PermissionError as DomainPermissionError,
    PortalError,
    UnreachableError,
    ValidationError,
)
from src.infrastructure.security.masking import sanitize_message


def translate_error(exc: Exception, *, secrets: Iterable[str] = ()) -> PortalError:
    """WinRM/WMI/VMM 예외를 도메인 예외로 변환하고 메시지에서 자격증명을 제거한다."""
    if isinstance(exc, PortalError):
        return exc

    msg = str(exc)
    lowered = msg.lower()

    if isinstance(exc, PypsrpAuthenticationError) or "401" in msg or "unauthorized" in lowered:
        return AuthenticationError("인증에 실패했습니다. 계정, 비밀번호, 인증 방식을 확인하세요.")
    if "access is denied" in lowered or "accessdenied" in lowered:
        return DomainPermissionError("원격 관리 권한이 부족합니다.")
    if isinstance(exc, ssl.SSLError):
        return UnreachableError(
            "TLS 인증서 검증에 실패했습니다. 자체 서명 인증서라면 검증을 비활성화하세요."
        )
    if isinstance(exc, (socket.timeout, asyncio.TimeoutError, TimeoutError)):
        return UnreachableError("응답 시간이 초과되었습니다.")
    if isinstance(exc, WinRMTransportError) or isinstance(
        exc, (socket.gaierror, ConnectionRefusedError, ConnectionError, OSError)
    ):
        return UnreachableError("WinRM 서비스에 연결할 수 없습니다. 포트와 방화벽을 확인하세요.")
    # 경로 B — 대상이 SCVMM 서버가 아니거나 콘솔이 없다. 연결 유형 선택 오류일 가능성이 높다.
    # **인증 실패로 판정하지 않는다** — 조치가 "자격증명 확인"이 되어 원인에서 멀어진다 (계획 05 §11).
    if "virtualmachinemanager" in lowered:
        return ValidationError(
            "대상 서버에서 SCVMM 모듈을 찾을 수 없습니다. "
            "SCVMM 관리 서버 주소가 맞는지, 연결 유형이 올바른지 확인하세요.",
            field="kind",
        )
    if "cannot find the type" in lowered or "is not recognized" in lowered:
        return CollectionError(
            "필요한 PowerShell 모듈이 없습니다 (Hyper-V / FailoverClusters / VirtualMachineManager)."
        )
    if isinstance(exc, (WSManFaultError, WinRMError)):
        return CollectionError(f"WinRM 오류: {sanitize_message(msg, secrets=secrets)}")
    return CollectionError(f"Hyper-V 수집 오류: {sanitize_message(msg, secrets=secrets)}")
