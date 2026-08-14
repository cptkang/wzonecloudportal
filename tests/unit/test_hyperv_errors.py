"""WinRM 예외 변환 테스트 (계획 05 §11, §12-1).

- 인증 실패 → AuthenticationError(retryable=False) — 계정 잠금 방지 (CST-05)
- 모듈 부재 → 인증 오류가 아니라 설정 오류 (연결 유형 재확인 유도)
- 자격증명이 메시지에 남지 않는다 (NFR-203)
"""

from __future__ import annotations

import ssl

from pypsrp.exceptions import AuthenticationError as PypsrpAuthError
from pypsrp.exceptions import WinRMTransportError

from src.domain.exceptions import (
    AuthenticationError,
    CollectionError,
    PermissionError as DomainPermissionError,
    UnreachableError,
    ValidationError,
)
from src.infrastructure.hyperv.errors import translate_error


def test_pypsrp_auth_exception_maps_by_type() -> None:
    err = translate_error(PypsrpAuthError("failed to authenticate"))
    assert isinstance(err, AuthenticationError)
    assert err.retryable is False


def test_http_401_maps_to_authentication() -> None:
    err = translate_error(RuntimeError("Code 401 unauthorized response"))
    assert isinstance(err, AuthenticationError)


def test_access_denied_maps_to_permission() -> None:
    err = translate_error(RuntimeError("Access is denied. (5)"))
    assert isinstance(err, DomainPermissionError)
    assert err.retryable is False


def test_transport_error_maps_to_unreachable() -> None:
    err = translate_error(WinRMTransportError("http", 502, "bad gateway"))
    assert isinstance(err, UnreachableError)
    assert err.retryable is True


def test_ssl_error_maps_to_unreachable_with_guidance() -> None:
    err = translate_error(ssl.SSLError("certificate verify failed"))
    assert isinstance(err, UnreachableError)


def test_missing_scvmm_module_is_config_error_not_auth() -> None:
    """연결 유형을 잘못 골랐을 때 가장 흔한 실패 — 인증 실패와 구분한다 (§10·§11)."""
    exc = RuntimeError(
        "The specified module 'VirtualMachineManager' was not loaded because no valid "
        "module file was found"
    )
    err = translate_error(exc)
    assert isinstance(err, ValidationError)
    assert err.field == "kind"


def test_missing_hyperv_module_is_collection_error() -> None:
    exc = RuntimeError("The term 'Get-VM' is not recognized as the name of a cmdlet")
    err = translate_error(exc)
    assert isinstance(err, CollectionError)
    assert "PowerShell 모듈" in err.message


def test_secrets_are_scrubbed_from_unknown_errors() -> None:
    exc = RuntimeError("connect failed for user svc with hunter2-secret somewhere")
    err = translate_error(exc, secrets=("hunter2-secret",))
    assert "hunter2-secret" not in err.message


def test_domain_errors_pass_through() -> None:
    original = AuthenticationError("이미 변환됨")
    assert translate_error(original) is original
