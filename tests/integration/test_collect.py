"""수집 통합 테스트 — Step 1 완료 기준의 핵심 (ROADMAP §13).

- 2회 수집 → VM 레코드 1건 (FR-303)
- 식별키 조회 경로로 갱신 (`ON CONFLICT` 아님) + `resource_identities`에 rule=1 행
- Tools 미실행 재수집에도 게스트 값과 `guest_observed_at` 유지 (ROADMAP §7.4)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from src.config import Settings, get_settings
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind, ConnectionStatus, GuestInfoAvailability
from src.infrastructure.db.models import ConnectionRow, ResourceIdentityRow, VirtualMachineRow
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository
from src.infrastructure.security.cipher import CredentialCipher
from src.infrastructure.security.keys import EnvKeyProvider
from tests.fakes.fake_reader import FakeInventoryReader, make_vm

from src.application.collect_service import CollectService


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def connection(session, settings: Settings) -> Connection:
    cipher = CredentialCipher(EnvKeyProvider(settings))
    repo = ConnectionRepository(session, cipher)
    conn = Connection(
        connection_id=uuid4(),
        kind=ConnectionKind.VCENTER,
        display_name="vCenter 테스트",
        address="vcsa-test.example.invalid",
        port=443,
        username="svc-inventory@vsphere.local",
        password=SecretStr("collect-test-password"),
    )
    await repo.insert(conn, cipher.encrypt(conn.password))
    await session.commit()
    return conn


def _service(session, reader: FakeInventoryReader, settings: Settings) -> CollectService:
    cipher = CredentialCipher(EnvKeyProvider(settings))
    return CollectService(
        ConnectionRepository(session, cipher),
        VirtualMachineRepository(session),
        lambda _conn: reader,
        settings,
    )


async def test_two_collections_produce_one_record(session, connection, settings) -> None:
    """FR-303 — 재수집이 중복 레코드를 만들지 않는다."""
    vms = [
        make_vm(connection_id=connection.connection_id, name="web-01", native_id="i-1"),
        make_vm(connection_id=connection.connection_id, name="web-02", native_id="i-2"),
    ]

    first = await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(connection)
    await session.commit()
    second = await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(connection)
    await session.commit()

    assert first.created == 2
    assert second.created == 0
    assert second.unchanged == 2

    total = await session.scalar(select(func.count()).select_from(VirtualMachineRow))
    assert total == 2


async def test_identity_rows_written_for_each_vm(session, connection, settings) -> None:
    """upsert가 `ON CONFLICT`가 아니라 식별키 조회 경로로 동작함을 보인다."""
    vms = [make_vm(connection_id=connection.connection_id, name=f"vm-{i}", native_id=f"i-{i}") for i in range(3)]
    await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(connection)
    await session.commit()

    rows = (await session.execute(select(ResourceIdentityRow))).scalars().all()
    assert len(rows) == 3
    assert {r.rule for r in rows} == {1}
    assert {r.key_value for r in rows} == {
        f"{connection.connection_id}:i-{i}" for i in range(3)
    }

    # 재수집해도 식별 행이 늘지 않는다
    await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(connection)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(ResourceIdentityRow)) == 3


async def test_guest_values_survive_tools_going_down(session, connection, settings) -> None:
    """ROADMAP §7.4 — 도구가 멈춰도 이전 값과 원래 관측 시각이 남아야 한다.

    이것이 깨지면 재수집 한 번으로 게스트 OS·호스트명이 NULL이 되고 복구할 수 없다.
    """
    earlier = datetime.now(UTC) - timedelta(days=3)
    healthy = make_vm(
        connection_id=connection.connection_id,
        name="db-01",
        native_id="i-db",
        os_name="Oracle Linux 8.9",
        hostname="db-01.example.invalid",
        observed_at=earlier,
    )
    await _service(session, FakeInventoryReader(connection, vms=[healthy]), settings).collect(connection)
    await session.commit()

    degraded = make_vm(
        connection_id=connection.connection_id,
        name="db-01",
        native_id="i-db",
        guest_availability=GuestInfoAvailability.TOOLS_NOT_RUNNING,
    )
    await _service(session, FakeInventoryReader(connection, vms=[degraded]), settings).collect(connection)
    await session.commit()

    row = (await session.execute(select(VirtualMachineRow))).scalar_one()
    assert row.guest_availability == GuestInfoAvailability.TOOLS_NOT_RUNNING.value
    assert row.guest_os_name == "Oracle Linux 8.9"
    assert row.guest_hostname == "db-01.example.invalid"
    # 현재 시각으로 갱신되면 "마지막 확인" 표시가 거짓말이 된다
    assert row.guest_observed_at == earlier


async def test_rename_updates_same_record(session, connection, settings) -> None:
    """이름이 바뀌어도 native_id가 같으면 동일 레코드다."""
    before = make_vm(connection_id=connection.connection_id, name="old-name", native_id="i-1")
    await _service(session, FakeInventoryReader(connection, vms=[before]), settings).collect(connection)
    await session.commit()

    after = make_vm(connection_id=connection.connection_id, name="new-name", native_id="i-1")
    result = await _service(session, FakeInventoryReader(connection, vms=[after]), settings).collect(connection)
    await session.commit()

    assert result.updated == 1
    row = (await session.execute(select(VirtualMachineRow))).scalar_one()
    assert row.name == "new-name"
    assert await session.scalar(select(func.count()).select_from(VirtualMachineRow)) == 1


async def test_auth_failure_is_not_retried_and_flags_connection(session, connection, settings) -> None:
    """FR-114·CST-05 — 인증 실패는 재시도하지 않는다. 반복하면 AD 계정이 잠긴다."""
    reader = FakeInventoryReader(connection, auth_error=True)
    result = await _service(session, reader, settings).collect(connection)
    await session.commit()

    assert result.failed is True
    assert reader.start_calls == 1  # 재시도 0회

    row = await session.get(ConnectionRow, connection.connection_id)
    assert row.status == ConnectionStatus.CREDENTIAL_ERROR.value
    assert row.last_error


async def test_collection_failure_preserves_existing_data(session, connection, settings) -> None:
    """NFR-302 — 실패해도 기존 수집 데이터를 삭제하지 않는다."""
    vms = [make_vm(connection_id=connection.connection_id, name="keep-me", native_id="i-1")]
    await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(connection)
    await session.commit()

    reader = FakeInventoryReader(connection, unreachable=True)
    result = await _service(session, reader, settings).collect(connection)
    await session.commit()

    assert result.failed is True
    assert await session.scalar(select(func.count()).select_from(VirtualMachineRow)) == 1


async def test_session_is_closed_even_on_failure(session, connection, settings) -> None:
    """수집 실패 시에도 vCenter 세션이 해제되어야 한다 (계획 04 §3.1)."""
    from src.domain.enums import ResourceType

    reader = FakeInventoryReader(
        connection, vms=[], fail_types={ResourceType.VIRTUAL_MACHINE}
    )
    await _service(session, reader, settings).collect(connection)
    await session.commit()

    assert reader.is_session_closed
