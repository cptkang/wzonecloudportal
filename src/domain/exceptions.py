"""도메인 예외 계층 (계획 02 §4). 축소하지 않고 처음부터 완성한다.

`retryable` 속성이 계정 잠금 방지(FR-114, CST-05)의 핵심이다.
재시도 데코레이터가 이 값만 보고 분기한다.
"""

from __future__ import annotations

from typing import Any

from src.domain.enums import ResourceType


class PortalError(Exception):
    """모든 도메인 예외의 기반."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ConnectionError(PortalError):
    """하이퍼바이저 연결 관련 오류."""

    retryable: bool = True


class AuthenticationError(ConnectionError):
    """인증 실패.

    재시도하면 AD 계정 잠금 정책에 걸려 서비스 계정이 잠기고,
    그 계정을 쓰는 다른 시스템까지 연쇄 장애가 발생한다 (CST-05).
    """

    retryable = False


class PermissionError(ConnectionError):
    """권한 부족. 자격증명은 유효하므로 재시도해도 결과가 같다."""

    retryable = False


class UnreachableError(ConnectionError):
    """네트워크·서버 오류. 일시적일 수 있으므로 재시도 대상."""

    retryable = True


class CollectionError(PortalError):
    """수집 중 오류 (파싱 실패, 예상치 못한 응답 등)."""

    def __init__(
        self,
        message: str,
        *,
        resource_type: ResourceType | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.resource_type = resource_type


class NotFoundError(PortalError):
    """대상 자원 없음. API에서 404로 매핑된다."""


class ValidationError(PortalError):
    """입력 검증 실패. API에서 422로 매핑된다."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.field = field


class DuplicateError(PortalError):
    """중복 등록 (FR-105). API에서 409로 매핑된다."""
