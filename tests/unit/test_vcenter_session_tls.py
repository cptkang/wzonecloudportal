"""TLS 컨텍스트 구성 (계획 04 §3.1, D-020).

`verify_tls=false`는 **인증서 검증만** 끄는 것이지 프로토콜 하한까지 낮추는 것이
아니다. VCF 9는 TLS 1.3이 기본이며 강화 프로파일을 쓰는 사이트도 있다.
"""

from __future__ import annotations

import ssl
from uuid import uuid4

from pydantic import SecretStr

from src.domain.connection import Connection
from src.domain.enums import ConnectionKind
from src.infrastructure.vcenter.session import VCenterSession


def _connection(*, verify_tls: bool) -> Connection:
    return Connection(
        connection_id=uuid4(),
        kind=ConnectionKind.VCENTER,
        display_name="vCenter 테스트",
        address="vcsa.example.invalid",
        port=443,
        username="svc-inventory@vsphere.local",
        password=SecretStr("tls-test-password"),
        verify_tls=verify_tls,
    )


def test_verify_tls_true_uses_default_verification() -> None:
    """검증을 켜면 pyVmomi 기본 컨텍스트를 쓴다 — 직접 만들지 않는다."""
    session = VCenterSession(_connection(verify_tls=True))
    assert session._build_ssl_context() is None


def test_verify_tls_false_still_requires_tls_1_2_or_higher() -> None:
    session = VCenterSession(_connection(verify_tls=False))
    ctx = session._build_ssl_context()

    assert ctx is not None
    assert ctx.minimum_version is ssl.TLSVersion.TLSv1_2
    # 자체 서명 인증서를 허용하는 것이 이 옵션의 목적이다
    assert ctx.verify_mode is ssl.CERT_NONE
    assert ctx.check_hostname is False
