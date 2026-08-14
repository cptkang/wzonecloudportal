"""vCenter 매퍼 (계획 04 §6.1·6.2).

`mapper.py`는 pyVmomi 타입을 import하지 않으므로 속성 dict만으로 검증할 수 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.enums import ConnectionState, GuestInfoAvailability, OsSource, PowerState
from src.infrastructure.vcenter.mapper import map_guest_info, map_virtual_machine, moref_or_none

OBSERVED = datetime(2026, 8, 7, 3, 0, tzinfo=UTC)


def _base_props(**overrides: object) -> dict[str, object]:
    props: dict[str, object] = {
        "name": "web-prd-01",
        "config.instanceUuid": "5001-instance",
        "config.uuid": "4213-bios",
        "config.guestFullName": "Ubuntu Linux (64-bit)",
        "config.hardware.numCPU": 4,
        "config.hardware.memoryMB": 8192,
        "runtime.powerState": "poweredOn",
        "runtime.connectionState": "connected",
        "guest.guestFullName": "Ubuntu 22.04 LTS",
        "guest.hostName": "web-prd-01.example.invalid",
        "guest.toolsStatus": "toolsOk",
        "guest.toolsRunningStatus": "guestToolsRunning",
    }
    props.update(overrides)
    return props


def test_maps_core_fields() -> None:
    vm = map_virtual_machine(uuid4(), "vm-1042", _base_props(), OBSERVED)

    assert vm.native_id == "5001-instance"
    assert vm.bios_uuid == "4213-bios"
    assert vm.power_state is PowerState.ON
    assert vm.connection_state is ConnectionState.CONNECTED
    assert vm.cpu.total_vcpu == 4
    assert vm.memory.assigned_mb == 8192
    assert vm.platform.configured_os == "Ubuntu Linux (64-bit)"


def test_falls_back_to_moref_when_instance_uuid_missing() -> None:
    """`missingSet`으로 온 속성은 None이다. 그때도 식별자가 있어야 한다."""
    vm = map_virtual_machine(uuid4(), "vm-1042", _base_props(**{"config.instanceUuid": None}), OBSERVED)
    assert vm.native_id == "vm-1042"


def test_unknown_power_state_is_not_guessed() -> None:
    vm = map_virtual_machine(uuid4(), "vm-1", _base_props(**{"runtime.powerState": None}), OBSERVED)
    assert vm.power_state is PowerState.UNKNOWN


def test_tools_not_installed() -> None:
    guest = map_guest_info(_base_props(**{"guest.toolsStatus": "toolsNotInstalled"}), OBSERVED)
    assert guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED
    assert guest.is_collected is False
    # 수집 불가일 때 값을 채우면 "값 없음"과 구분되지 않는다
    assert guest.os_name is None
    assert guest.observed_at is None


@pytest.mark.parametrize("running", ["guestToolsNotRunning", "guestToolsExecutingScripts"])
def test_tools_not_running(running: str) -> None:
    guest = map_guest_info(_base_props(**{"guest.toolsRunningStatus": running}), OBSERVED)
    assert guest.availability is GuestInfoAvailability.TOOLS_NOT_RUNNING


def test_status_ok_but_no_values_is_unknown() -> None:
    """상태 보고가 부정확한 알려진 버그가 있으므로 값 유무도 함께 본다 (조사 §6.3)."""
    guest = map_guest_info(
        _base_props(**{"guest.hostName": None, "guest.guestFullName": None}), OBSERVED
    )
    assert guest.availability is GuestInfoAvailability.UNKNOWN


def test_os_source_marks_config_fallback() -> None:
    guest = map_guest_info(
        _base_props(**{"guest.guestFullName": None, "guest.hostName": "host.example.invalid"}),
        OBSERVED,
    )
    assert guest.availability is GuestInfoAvailability.AVAILABLE
    assert guest.os_name == "Ubuntu Linux (64-bit)"
    assert guest.os_source is OsSource.VM_CONFIG


def test_moref_or_none() -> None:
    class FakeMoRef:
        _moId = "host-1042"

    assert moref_or_none(FakeMoRef()) == "host-1042"
    assert moref_or_none(None) is None
