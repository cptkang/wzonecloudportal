"""리더 계약·부분 실패 테스트 (계획 05 §9·§10, §12-9·18·19, 계획 03 §9).

실제 WinRM 없이(CST-04) 실행기를 스텁으로 바꿔 리더의 **행동 계약**을 확인한다:
- 세 어댑터 모두 `HypervisorInventoryReader` Protocol을 만족한다
- 인증 실패는 전파되고(계정 잠금 방지), 그 외 실패는 outcome으로 보고된다 (FR-204)
- 클러스터는 노드 일부가 실패해도 나머지를 계속 수집한다 (§9)
- VMId 없는 SCVMM VM은 제외되고 건수가 error에 남는다 (§8.4)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from src.config import get_settings
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind, ResourceType, WinRmAuth
from src.domain.exceptions import AuthenticationError, UnreachableError, ValidationError
from src.domain.ports import HypervisorInventoryReader
from src.infrastructure.hyperv import HyperVHostInventoryReader, ScvmmInventoryReader
from src.infrastructure.hyperv import host_reader as host_reader_module

VM_GUID = "a1b2c3d4-0000-1111-2222-333344445555"


def make_conn(
    kind: ConnectionKind,
    address: str = "hv01.example.invalid",
) -> Connection:
    if kind is ConnectionKind.VCENTER:
        return Connection(
            connection_id=uuid4(),
            kind=kind,
            display_name="테스트 vCenter",
            address=address,
            port=443,
            username="svc-inventory@vsphere.local",
            password=SecretStr("test-password-123"),
        )
    return Connection(
        connection_id=uuid4(),
        kind=kind,
        display_name="테스트 Hyper-V",
        address=address,
        port=5986,
        username="DOMAIN\\svc-inventory",
        password=SecretStr("test-password-123"),
        auth_method=WinRmAuth.NTLM,
    )


class StubRunner:
    """스크립트와 무관하게 지정된 결과를 돌려주거나 예외를 던진다."""

    def __init__(self, result: list[dict] | Exception) -> None:
        self._result = result

    async def invoke_json(self, script: str, params: dict[str, Any] | None = None) -> list[dict]:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ── 계약: Protocol 준수 (계획 03 §9 — 세 어댑터 파라미터화) ──


@pytest.mark.parametrize(
    "kind",
    [
        ConnectionKind.VCENTER,
        ConnectionKind.SCVMM,
        ConnectionKind.HYPERV_HOST,
        ConnectionKind.HYPERV_CLUSTER,
    ],
)
def test_every_adapter_satisfies_the_reader_protocol(kind: ConnectionKind) -> None:
    settings = get_settings()
    conn = make_conn(kind)
    if kind is ConnectionKind.VCENTER:
        from src.infrastructure.vcenter import VCenterInventoryReader

        reader: object = VCenterInventoryReader(conn, settings)
    elif kind is ConnectionKind.SCVMM:
        reader = ScvmmInventoryReader(conn, settings)
    else:
        reader = HyperVHostInventoryReader(conn, settings)
    assert isinstance(reader, HypervisorInventoryReader)
    assert reader.connection_id == conn.connection_id


# ── 경로 B — SCVMM ───────────────────────────────────────────


async def _collect(reader: ScvmmInventoryReader | HyperVHostInventoryReader) -> list:
    return [vm async for vm in reader.list_virtual_machines()]


async def test_scvmm_vms_without_vmid_are_excluded_and_counted() -> None:
    reader = ScvmmInventoryReader(make_conn(ConnectionKind.SCVMM), get_settings())
    reader._runner = StubRunner(  # type: ignore[assignment]
        [
            {"Id": VM_GUID, "Name": "vm-ok", "State": "Running"},
            {"Id": None, "Name": "vm-undeployed", "State": "Stored"},
        ]
    )
    vms = await _collect(reader)
    assert [vm.native_id for vm in vms] == [VM_GUID]

    outcome = reader.get_outcomes()[0]
    assert outcome.resource_type is ResourceType.VIRTUAL_MACHINE
    assert outcome.collected_count == 1
    assert outcome.failed is False
    assert outcome.error is not None and "1건" in outcome.error


async def test_scvmm_collection_failure_becomes_outcome_not_exception() -> None:
    """한 유형의 실패가 예외로 전파되면 안 된다 (FR-204)."""
    reader = ScvmmInventoryReader(make_conn(ConnectionKind.SCVMM), get_settings())
    reader._runner = StubRunner(UnreachableError("접속 불가"))  # type: ignore[assignment]
    assert await _collect(reader) == []
    outcome = reader.get_outcomes()[0]
    assert outcome.failed is True
    assert outcome.error == "접속 불가"


async def test_scvmm_auth_error_propagates() -> None:
    """인증 실패는 outcome이 아니라 전파다 — 연결을 자격증명 오류로 전환한다 (FR-114)."""
    reader = ScvmmInventoryReader(make_conn(ConnectionKind.SCVMM), get_settings())
    reader._runner = StubRunner(AuthenticationError("인증 실패"))  # type: ignore[assignment]
    with pytest.raises(AuthenticationError):
        await _collect(reader)


async def test_scvmm_probe_module_missing_is_config_error() -> None:
    """대상이 SCVMM이 아니면 인증 실패가 아니라 설정 오류다 (§10)."""
    reader = ScvmmInventoryReader(make_conn(ConnectionKind.SCVMM), get_settings())
    reader._runner = StubRunner([{"module": False, "error": "module not found"}])  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        await reader._check_authorized(set())


async def test_scvmm_probe_success_reports_version_and_readable_types() -> None:
    reader = ScvmmInventoryReader(make_conn(ConnectionKind.SCVMM), get_settings())
    reader._runner = StubRunner(  # type: ignore[assignment]
        [{"module": True, "version": "10.22.1287.0", "role": "ReadOnlyAdmin", "vm": True}]
    )
    readable: set[ResourceType] = set()
    detail = await reader._check_authorized(readable)
    assert readable == {ResourceType.VIRTUAL_MACHINE}
    assert reader._server_version == "SCVMM 10.22.1287.0"
    assert detail is not None and "ReadOnlyAdmin" in detail


# ── 경로 A — 클러스터 노드 순회 (§9) ─────────────────────────


CLUSTER_NODES_PAYLOAD = [
    {
        "ClusterName": "HVC01",
        "Nodes": [
            {"Name": "n1.example.invalid", "State": "Up", "Id": "1"},
            {"Name": "n2.example.invalid", "State": "Up", "Id": "2"},
            {"Name": "n3.example.invalid", "State": "Down", "Id": "3"},
        ],
    }
]


def _install_node_fakes(
    monkeypatch: pytest.MonkeyPatch, behavior: dict[str, list[dict] | Exception]
) -> None:
    """노드별 세션·실행기를 페이크로 바꾼다. behavior는 주소 → 결과 또는 예외."""

    class FakeNodeSession:
        def __init__(self, conn: Connection, settings: object) -> None:
            self.conn = conn

        async def start_session(self) -> None:
            pass

        async def close_session(self) -> None:
            pass

    class FakeNodeRunner:
        def __init__(self, session: FakeNodeSession) -> None:
            self._session = session

        async def invoke_json(
            self, script: str, params: dict[str, Any] | None = None
        ) -> list[dict]:
            result = behavior[self._session.conn.address]
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(host_reader_module, "HyperVSession", FakeNodeSession)
    monkeypatch.setattr(host_reader_module, "PowerShellRunner", FakeNodeRunner)


async def test_cluster_partial_node_failure_keeps_collecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """노드 1개 실패 + 1개 다운이어도 나머지 노드의 VM은 수집된다 (§9, §12-18)."""
    reader = HyperVHostInventoryReader(
        make_conn(ConnectionKind.HYPERV_CLUSTER, address="hvc01.example.invalid"), get_settings()
    )
    reader._runner = StubRunner(CLUSTER_NODES_PAYLOAD)  # type: ignore[assignment]
    _install_node_fakes(
        monkeypatch,
        {
            "n1.example.invalid": [
                {"Id": VM_GUID, "Name": "vm1", "State": 2, "IntegrationOk": False},
                {"Id": str(uuid4()), "Name": "vm2", "State": 3, "IntegrationOk": False},
            ],
            "n2.example.invalid": UnreachableError("노드 접속 불가"),
        },
    )

    vms = await _collect(reader)
    assert len(vms) == 2

    outcome = reader.get_outcomes()[0]
    assert outcome.collected_count == 2
    # 일부 노드만 실패하면 failed=False — 수집된 VM이 저장되어야 한다 (§9)
    assert outcome.failed is False
    assert outcome.error is not None
    assert "n2.example.invalid" in outcome.error
    assert "n3.example.invalid" in outcome.error  # 다운 노드도 관리자에게 보인다


async def test_cluster_total_failure_is_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = HyperVHostInventoryReader(
        make_conn(ConnectionKind.HYPERV_CLUSTER, address="hvc01.example.invalid"), get_settings()
    )
    reader._runner = StubRunner(CLUSTER_NODES_PAYLOAD)  # type: ignore[assignment]
    _install_node_fakes(
        monkeypatch,
        {
            "n1.example.invalid": UnreachableError("불가"),
            "n2.example.invalid": UnreachableError("불가"),
        },
    )
    assert await _collect(reader) == []
    assert reader.get_outcomes()[0].failed is True


async def test_cluster_node_auth_error_stops_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 자격증명이 모든 노드에서 실패한다 — 노드마다 재시도하면 계정이 잠긴다 (CST-05)."""
    reader = HyperVHostInventoryReader(
        make_conn(ConnectionKind.HYPERV_CLUSTER, address="hvc01.example.invalid"), get_settings()
    )
    reader._runner = StubRunner(CLUSTER_NODES_PAYLOAD)  # type: ignore[assignment]
    _install_node_fakes(
        monkeypatch,
        {
            "n1.example.invalid": AuthenticationError("인증 실패"),
            "n2.example.invalid": [{"Id": VM_GUID, "Name": "vm1", "State": 2}],
        },
    )
    with pytest.raises(AuthenticationError):
        await _collect(reader)


async def test_single_host_collects_via_main_session() -> None:
    reader = HyperVHostInventoryReader(make_conn(ConnectionKind.HYPERV_HOST), get_settings())
    reader._runner = StubRunner(  # type: ignore[assignment]
        [{"Id": VM_GUID, "Name": "vm1", "State": 2, "IntegrationOk": False}]
    )
    vms = await _collect(reader)
    assert len(vms) == 1
    assert reader.get_outcomes()[0].failed is False
