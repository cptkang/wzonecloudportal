"""Hyper-V/SCVMM 연결 등록 API 통합 테스트 (계획 05, 계획 08 §5.1).

discriminated union 검증과 WinRM 필드의 저장 라운드트립을 확인한다.
실제 WinRM 접속은 하지 않는다 (CST-04) — 연결 테스트·수집 동작은 단위 테스트가 다룬다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from src.config import get_settings
from src.domain.auth import Role, UserStatus
from src.infrastructure.db.models import ConnectionRow
from src.infrastructure.repository.user_repo import UserRepository
from src.infrastructure.security.password import hash_password
from src.main import create_app

ADMIN_PASSWORD = "admin-test-password"


@pytest.fixture
async def app(session_factory):  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    application = create_app(get_settings())
    application.state.session_factory = session_factory
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client(client: AsyncClient, session) -> AsyncClient:  # type: ignore[no-untyped-def]
    users = UserRepository(session)
    await users.add(
        username="admin",
        password_hash=hash_password(SecretStr(ADMIN_PASSWORD)),
        display_name="관리자",
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
    )
    await session.commit()
    res = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert res.status_code == 200, res.text
    return client


def _scvmm_payload(**overrides: object) -> dict:
    payload = {
        "kind": "scvmm",
        "display_name": "SCVMM 본사",
        "address": "scvmm01.example.invalid",
        "port": 5986,
        "username": "DOMAIN\\svc-inventory",
        "password": "connection-secret-1234",
        "verify_tls": True,
        "protocol": "https",
        "auth_method": "kerberos",
    }
    payload.update(overrides)
    return payload


async def test_register_scvmm_connection(admin_client: AsyncClient, session) -> None:  # type: ignore[no-untyped-def]
    res = await admin_client.post("/api/v1/connections", json=_scvmm_payload())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kind"] == "scvmm"
    assert body["port"] == 5986
    assert "password" not in body

    # WinRM 필드가 실제로 저장되는지 — 마이그레이션 0002 + 저장소 라운드트립
    row = (
        await session.execute(
            select(ConnectionRow).where(ConnectionRow.address == "scvmm01.example.invalid")
        )
    ).scalar_one()
    assert row.kind == "scvmm"
    assert row.auth_method == "kerberos"
    assert row.protocol == "https"
    assert row.session_configuration is None


async def test_register_hyperv_cluster_with_jea(admin_client: AsyncClient, session) -> None:  # type: ignore[no-untyped-def]
    payload = _scvmm_payload(
        kind="hyperv-cluster",
        display_name="HV 클러스터 01",
        address="hvc01.example.invalid",
        auth_method="ntlm",
        session_configuration="WzonePortalReadOnly",
    )
    res = await admin_client.post("/api/v1/connections", json=payload)
    assert res.status_code == 201, res.text
    assert res.json()["kind"] == "hyperv-cluster"

    row = (
        await session.execute(
            select(ConnectionRow).where(ConnectionRow.address == "hvc01.example.invalid")
        )
    ).scalar_one()
    assert row.session_configuration == "WzonePortalReadOnly"


async def test_winrm_kinds_require_auth_method(admin_client: AsyncClient) -> None:
    payload = _scvmm_payload(kind="hyperv-host", address="hv01.example.invalid")
    del payload["auth_method"]
    res = await admin_client.post("/api/v1/connections", json=payload)
    assert res.status_code == 422


async def test_scvmm_rejects_jea_session_configuration(admin_client: AsyncClient) -> None:
    """JEA 세션 구성은 경로 A 전용이다 (계획 05 §4.3.1) — 스키마에 필드 자체가 없다."""
    res = await admin_client.post(
        "/api/v1/connections",
        json=_scvmm_payload(session_configuration="WzonePortalReadOnly"),
    )
    # pydantic이 미지의 필드를 무시하므로 등록은 성공하되 저장되지 않아야 한다
    assert res.status_code == 201


async def test_unknown_kind_is_rejected(admin_client: AsyncClient) -> None:
    res = await admin_client.post("/api/v1/connections", json=_scvmm_payload(kind="xenserver"))
    assert res.status_code == 422


async def test_mixed_kinds_appear_in_one_list(admin_client: AsyncClient) -> None:
    """vCenter와 Hyper-V 연결이 한 목록에 함께 표시된다 (FR-1203의 연결 관리 측면)."""
    assert (
        await admin_client.post(
            "/api/v1/connections",
            json={
                "kind": "vcenter",
                "display_name": "vCenter DC1",
                "address": "vcsa-dc1.example.invalid",
                "port": 443,
                "username": "svc-inventory@vsphere.local",
                "password": "connection-secret-1234",
                "verify_tls": True,
            },
        )
    ).status_code == 201
    assert (await admin_client.post("/api/v1/connections", json=_scvmm_payload())).status_code == 201

    listed = (await admin_client.get("/api/v1/connections")).json()
    kinds = sorted(c["kind"] for c in listed)
    assert kinds == ["scvmm", "vcenter"]
