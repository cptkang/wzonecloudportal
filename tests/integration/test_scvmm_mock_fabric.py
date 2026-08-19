"""SCVMM 목업 fabric 관통 테스트 (docs/06_scvmm_test_environment.md).

실제 SCVMM은 개발 PC에 세울 수 없다 — Windows Server + SQL Server + 도메인이 필요하고
시뮬레이터도 컨테이너도 없다 (D-018, docs/06 §3). 이 테스트는 그 공백에서 얻을 수 있는
**가장 높은 충실도**로, `ScvmmInventoryReader`의 수집 경로 전체를 관통한다:

    스크립트 원본 → 실제 PowerShell 5.1 → 목 cmdlet → ConvertTo-Json
      → parse_ps_json → VMId 제외 로직 → scvmm_mapper → 도메인 모델 → outcome

`tests/integration/test_ps_scripts_live.py`와의 차이: 그쪽은 스크립트와 매퍼를 직접 호출하고
리더를 건너뛴다. 여기서는 **리더를 통과**하므로 제외 로직·부분 실패·outcome·예외 변환까지 확인된다.

검증하지 **못하는** 것: WinRM 전송·인증·세션 수명, 실제 VMM 객체 모델
(연구 노트 §11-11~15 — 실환경 실측 항목).

Windows가 아니면 전체 skip된다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from src.config import get_settings
from src.domain.connection import Connection
from src.domain.enums import (
    ConnectionKind,
    GuestInfoAvailability,
    OsSource,
    PowerState,
    ResourceType,
    WinRmAuth,
)
from src.domain.exceptions import PortalError, ValidationError
from src.domain.resource import VirtualMachine
from src.infrastructure.hyperv import ScvmmInventoryReader
from tests.fakes.local_ps_runner import POWERSHELL, LocalPowerShellRunner

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell 5.1이 필요한 목업 fabric 실행"
)

#: VMId가 없어 리더가 제외해야 하는 VM (§8.4)
UNDEPLOYED_VM_NAME = "mock-scvm-stored"


def make_scvmm_connection() -> Connection:
    return Connection(
        connection_id=uuid4(),
        kind=ConnectionKind.SCVMM,
        display_name="목업 SCVMM",
        address="mock-vmm01.example.invalid",
        port=5986,
        username="CONTOSO\\svc-inventory",
        password=SecretStr("mock-password-not-real"),
        auth_method=WinRmAuth.KERBEROS,
    )


def make_reader(scenario: str = "normal") -> ScvmmInventoryReader:
    """리더에 목 러너를 주입한다. 프로덕션 코드는 바꾸지 않는다 (docs/06 §5)."""
    reader = ScvmmInventoryReader(make_scvmm_connection(), get_settings())
    reader._runner = LocalPowerShellRunner(scenario=scenario)  # type: ignore[assignment]
    return reader


async def collect(reader: ScvmmInventoryReader) -> list[VirtualMachine]:
    return [vm async for vm in reader.list_virtual_machines()]


def by_name(vms: list[VirtualMachine], name: str) -> VirtualMachine:
    return next(vm for vm in vms if vm.name == name)


# ── 수집 경로 관통 ───────────────────────────────────────────


@pytest.fixture(scope="module")
async def normal_vms() -> list[VirtualMachine]:
    return await collect(make_reader("normal"))


async def test_undeployed_vm_is_excluded(normal_vms: list[VirtualMachine]) -> None:
    """VMId 없는 VM을 수집하면 재수집마다 중복이 쌓인다 (§8.4)."""
    names = [vm.name for vm in normal_vms]
    assert UNDEPLOYED_VM_NAME not in names
    assert len(normal_vms) == 5


async def test_native_id_is_the_hyperv_vm_guid(normal_vms: list[VirtualMachine]) -> None:
    """경로 A와 같은 VM에 같은 식별자를 주어야 중복 후보 감지가 동작한다 (§8.4)."""
    vm = by_name(normal_vms, "mock-scvm-running")
    assert vm.native_id == "a1b2c3d4-0000-1111-2222-333344445555"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mock-scvm-running", PowerState.ON),
        ("mock-scvm-off", PowerState.OFF),
        ("mock-scvm-paused", PowerState.SUSPENDED),
        ("mock-scvm-saved", PowerState.SUSPENDED),
        # 매핑되지 않는 상태는 추정하지 않고 UNKNOWN으로 둔다 (§8.1)
        ("mock-scvm-deploying", PowerState.UNKNOWN),
    ],
)
async def test_power_state_mapping(
    normal_vms: list[VirtualMachine], name: str, expected: PowerState
) -> None:
    assert by_name(normal_vms, name).power_state is expected


async def test_link_local_addresses_are_dropped(normal_vms: list[VirtualMachine]) -> None:
    """169.254/fe80은 자원 식별에 쓸 수 없다 — 매퍼가 걸러야 한다."""
    vm = by_name(normal_vms, "mock-scvm-running")
    assert vm.guest.ipv4_addresses == ("10.10.0.5",)
    assert vm.guest.ipv6_addresses == ()


async def test_guest_without_os_and_ip_is_marked_unavailable(
    normal_vms: list[VirtualMachine],
) -> None:
    """"값 없음"이 아니라 "수집 불가"로 구분되어야 한다 (FR-501)."""
    vm = by_name(normal_vms, "mock-scvm-saved")
    assert vm.guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED
    assert vm.guest.os_name is None


async def test_scvmm_os_is_reported_as_config_value(normal_vms: list[VirtualMachine]) -> None:
    """SCVMM 값은 KVP 실시간이 아니라 VMM DB 캐시다 (§8.5 [검증 필요])."""
    vm = by_name(normal_vms, "mock-scvm-off")
    assert vm.guest.availability is GuestInfoAvailability.AVAILABLE
    assert vm.guest.os_source is OsSource.VM_CONFIG
    assert vm.guest.os_name == "CentOS Linux 7 (64 bit)"


async def test_memory_is_read_as_megabytes(normal_vms: list[VirtualMachine]) -> None:
    """SCVMM의 Memory는 MB, 경로 A의 MemoryAssigned는 바이트다. 혼동하면 1024배 틀린다."""
    assert by_name(normal_vms, "mock-scvm-running").memory.assigned_mb == 8192
    assert by_name(normal_vms, "mock-scvm-paused").memory.assigned_mb == 16384


async def test_host_native_id_comes_from_vmhost(normal_vms: list[VirtualMachine]) -> None:
    assert by_name(normal_vms, "mock-scvm-running").host_native_id == "mock-hv01.example.invalid"


async def test_outcome_reports_excluded_count() -> None:
    reader = make_reader("normal")
    await collect(reader)
    outcome = reader.get_outcomes()[0]
    assert outcome.resource_type is ResourceType.VIRTUAL_MACHINE
    assert outcome.collected_count == 5
    assert outcome.failed is False
    assert outcome.error is not None and "1건 제외" in outcome.error


# ── 경계 시나리오 ────────────────────────────────────────────


async def test_single_vm_json_object_is_normalized() -> None:
    """PowerShell은 항목이 1개면 배열이 아닌 객체를 낸다 (§5.1)."""
    vms = await collect(make_reader("single"))
    assert len(vms) == 1 and vms[0].name == "mock-scvm-running"


async def test_empty_fabric_is_not_a_failure() -> None:
    """VM이 0대인 것과 수집 실패는 다르다."""
    reader = make_reader("empty")
    assert await collect(reader) == []
    outcome = reader.get_outcomes()[0]
    assert outcome.collected_count == 0
    assert outcome.failed is False


async def test_large_fabric_streams_all_vms() -> None:
    """대량 fabric에서 JSON 파싱·스트리밍이 끊기지 않는지 확인한다."""
    reader = make_reader("large")
    vms = await collect(reader)
    assert len(vms) == 500
    assert len({vm.native_id for vm in vms}) == 500  # 식별자 충돌 없음
    assert reader.get_outcomes()[0].failed is False


# ── 부분 실패 (FR-204) ───────────────────────────────────────


async def test_permission_failure_is_reported_as_outcome_not_exception() -> None:
    """수집 실패가 예외로 터지면 다른 연결의 수집까지 중단된다 (FR-204)."""
    reader = make_reader("no_permission")
    vms = await collect(reader)
    assert vms == []
    outcome = reader.get_outcomes()[0]
    assert outcome.failed is True
    assert outcome.error


# ── 연결 테스트 프로브 (FR-106, §10) ─────────────────────────


async def test_probe_reports_readable_types_and_role() -> None:
    reader = make_reader("normal")
    readable: set[ResourceType] = set()
    detail = await reader._check_authorized(readable)
    assert ResourceType.VIRTUAL_MACHINE in readable
    assert detail is not None and "ReadOnlyAdmin" in detail


async def test_missing_module_is_a_wrong_target_not_an_auth_failure() -> None:
    """SCVMM이 아닌 서버를 등록한 경우다. 인증 실패로 표시하면 관리자가 엉뚱한 조치를 한다 (§10)."""
    reader = make_reader("no_module")
    with pytest.raises(ValidationError) as exc:
        await reader._check_authorized(set())
    assert exc.value.field == "kind"


async def test_connection_failure_surfaces_as_portal_error() -> None:
    reader = make_reader("connect_fail")
    with pytest.raises(PortalError):
        await reader._check_authorized(set())


# ── 읽기 전용 강제 (D-005, §14) ──────────────────────────────


async def test_collection_scripts_invoke_no_write_cmdlets() -> None:
    """목 fabric은 쓰기 cmdlet 호출 시 즉시 예외를 던진다.

    수집이 성공했다는 것이 곧 쓰기 cmdlet을 부르지 않았다는 실행 시점의 증거다.
    grep 검사(§14)는 소스 문자열만 보므로 이 테스트가 그 빈틈을 메운다.
    """
    reader = make_reader("normal")
    assert len(await collect(reader)) == 5


async def test_write_cmdlet_trap_actually_fires() -> None:
    """트랩 자체가 동작하지 않으면 위 테스트는 아무것도 보장하지 못한다."""
    runner = LocalPowerShellRunner(scenario="normal")
    with pytest.raises(PortalError) as exc:
        await runner.invoke_json("Set-SCVirtualMachine -Name 'mock-scvm-running'")
    assert "읽기 전용 위반" in str(exc.value.message)
