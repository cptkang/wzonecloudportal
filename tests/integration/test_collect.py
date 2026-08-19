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

from src.application.collect_service import CollectService
from src.config import Settings, get_settings
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind, ConnectionStatus, GuestInfoAvailability
from src.infrastructure.db.models import ConnectionRow, ResourceIdentityRow, VirtualMachineRow
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository
from src.infrastructure.security.cipher import CredentialCipher
from src.infrastructure.security.keys import EnvKeyProvider
from tests.fakes.fake_reader import FakeInventoryReader, make_vm


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


# ── 예상하지 못한 예외 (T4) ──────────────────────────────────────
#
# `CollectService`는 자신이 아는 실패(인증·권한·도달 불가)를 이미 기록한다.
# 아래는 **그 밖의 예외** — DB 오류·매핑 버그 등 포탈 쪽 문제 — 를 다룬다.
# 기록되지 않으면 UI 폴링이 `last_error`를 보지 못해 "수집 중…"이 풀리지 않는다.


async def test_unexpected_exception_is_recorded_on_the_connection(
    session_factory, session, connection, settings
) -> None:
    from src.api.routes.connections import _run_collection

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("예상치 못한 오류")

    # `CollectService`가 잡지 못하고 통과시키는 예외를 흉내낸다.
    # 리더 팩토리는 `_run_collection` 내부에서 만들어지므로 서비스 단에서 주입한다.
    original = CollectService.collect
    CollectService.collect = boom  # type: ignore[method-assign]
    try:
        await _run_collection(session_factory, settings, connection.connection_id)
    finally:
        CollectService.collect = original  # type: ignore[method-assign]

    await session.rollback()  # 다른 세션이 쓴 값을 읽기 위해 스냅샷을 새로 뜬다
    row = await session.get(ConnectionRow, connection.connection_id)
    assert row.last_error, "예외가 연결 상태에 남지 않으면 UI가 '수집 중…'에 머문다"
    assert row.status == ConnectionStatus.COLLECTION_ERROR.value


async def test_unexpected_failure_message_has_no_credentials(
    session_factory, session, connection, settings
) -> None:
    """오류 메시지에 자격증명이 섞이면 안 된다 (NFR-203)."""
    secret = "collect-test-password"  # `connection` 픽스처가 쓰는 값

    async def leak(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"연결 실패 (password={secret})")

    original = CollectService.collect
    CollectService.collect = leak  # type: ignore[method-assign]
    try:
        await _run_collection_for(session_factory, settings, connection)
    finally:
        CollectService.collect = original  # type: ignore[method-assign]

    await session.rollback()
    row = await session.get(ConnectionRow, connection.connection_id)
    assert row.last_error
    assert secret not in row.last_error


async def _run_collection_for(session_factory, settings, connection) -> None:
    from src.api.routes.connections import _run_collection

    await _run_collection(session_factory, settings, connection.connection_id)


async def test_new_attempt_clears_the_previous_error(session, connection, settings) -> None:
    """이전 오류를 지우지 않으면 재수집이 **시작하자마자** 실패로 보인다.

    UI(`connections.js`의 `connState`)는 `last_error`만 보고 실패를 판정한다.
    """
    cipher = CredentialCipher(EnvKeyProvider(settings))
    repo = ConnectionRepository(session, cipher)

    await repo.mark_failure(
        connection.connection_id, "이전 수집 오류", ConnectionStatus.UNREACHABLE
    )
    await session.commit()

    await repo.mark_attempt(connection.connection_id)
    await session.commit()

    row = await session.get(ConnectionRow, connection.connection_id)
    assert row.last_error is None
    assert row.last_attempt_at is not None


async def test_collection_logs_adapter_and_db_time_separately(
    session, connection, settings
) -> None:
    """총 시간만 재면 병목이 vCenter 왕복인지 DB인지 알 수 없다 (ROADMAP §15.3-13).

    caplog 대신 로거에 핸들러를 직접 붙인다 — 어떤 로거를 보는지 명시된다.
    """
    import logging

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    vms = [
        make_vm(connection_id=connection.connection_id, name=f"vm-{i}", native_id=f"i-{i}")
        for i in range(3)
    ]

    log = logging.getLogger("src.application.collect_service")
    handler = _Capture()
    previous = log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        await _service(session, FakeInventoryReader(connection, vms=vms), settings).collect(
            connection
        )
        await session.commit()
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)

    matches = [r for r in records if r.msg == "수집 구간별 소요"]
    assert matches, f"계측 로그 없음. 기록된 것: {[r.msg for r in records]}"
    record = matches[0]

    # 두 구간이 **분리**되어야 한다. 합쳐진 값 하나로는 무엇을 고칠지 정할 수 없다.
    assert hasattr(record, "adapter_ms")
    assert hasattr(record, "db_ms")
    assert record.vm_count == 3
    assert record.batch_count >= 1

    # 인벤토리 정보 자체가 민감하다 — 개별 VM 이름이 로그에 남으면 안 된다 (NFR-206)
    assert all("vm-0" not in str(getattr(r, "msg", "")) for r in records)
