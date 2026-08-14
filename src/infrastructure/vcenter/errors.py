"""pyVmomi 예외 → 도메인 예외 (계획 04 §8).

**pyVmomi 예외가 어댑터 밖으로 나가면 안 된다.** 메시지에 접속 정보가 섞일 수 있고,
상위 계층이 pyVmomi 타입에 결합된다.

`InvalidLogin` → `AuthenticationError` 매핑이 계정 잠금 방지의 출발점이다
(FR-114, CST-05).
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Iterable

from pyVmomi import vim, vmodl

from src.domain.exceptions import (
    AuthenticationError,
    CollectionError,
    PermissionError as DomainPermissionError,
    PortalError,
    UnreachableError,
)
from src.infrastructure.security.masking import sanitize_message


def translate_error(exc: Exception, *, secrets: Iterable[str] = ()) -> PortalError:
    """pyVmomi 예외를 도메인 예외로 변환하고 메시지에서 자격증명을 제거한다."""
    if isinstance(exc, PortalError):
        return exc
    if isinstance(exc, vim.fault.InvalidLogin):
        return AuthenticationError("인증에 실패했습니다. 계정 또는 비밀번호를 확인하세요.")
    if isinstance(exc, vim.fault.NoPermission):
        priv = getattr(exc, "privilegeId", None)
        return DomainPermissionError(
            "조회 권한이 부족합니다." + (f" (필요 권한: {priv})" if priv else "")
        )
    if isinstance(exc, vim.fault.HostConnectFault):
        return UnreachableError("vCenter에 연결할 수 없습니다.")
    if isinstance(exc, ssl.SSLError):
        return UnreachableError(
            "TLS 인증서 검증에 실패했습니다. 자체 서명 인증서라면 검증을 비활성화하세요."
        )
    if isinstance(exc, (socket.timeout, asyncio.TimeoutError, TimeoutError)):
        return UnreachableError("응답 시간이 초과되었습니다.")
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, OSError)):
        return UnreachableError("네트워크 연결에 실패했습니다.")
    if isinstance(exc, vmodl.MethodFault):
        return CollectionError(
            f"vCenter 오류: {sanitize_message(getattr(exc, 'msg', '') or str(exc), secrets=secrets)}"
        )
    return CollectionError(f"알 수 없는 오류: {sanitize_message(str(exc), secrets=secrets)}")
