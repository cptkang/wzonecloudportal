"""경로 A(host_mapper)·경로 B(scvmm_mapper) 매핑 테스트 (계획 05 §8, §12-6·7·14~16).

핵심 확인 사항:
- 게스트 3분법 (FR-501): KVP/캐시 값 유무에 따른 TOOLS_NOT_INSTALLED / TOOLS_NOT_RUNNING / AVAILABLE
- 경로 B의 OS 출처는 vm_config (§8.5), 경로 A는 guest_tools
- 메모리 단위: 경로 A는 바이트, 경로 B는 MB (§8.3·§7.2)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.enums import Firmware, GuestInfoAvailability, OsSource, PowerState
from src.infrastructure.hyperv import host_mapper, scvmm_mapper

NOW = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
CONN = uuid4()


def _host_row(**overrides: object) -> dict:
    row = {
        "Id": "a1b2c3d4-0000-1111-2222-333344445555",
        "Name": "hv-vm01",
        "State": 2,
        "Generation": 2,
        "Version": "10.0",
        "ProcessorCount": 4,
        "MemoryAssigned": 8 * 1024**3,
        "MemoryStartup": 4 * 1024**3,
        "ComputerName": "HV-HOST01",
        "BiosGuid": "{4C4C4544-004D-3510-8054-B4C04F435831}",
        "IntegrationOk": True,
        "KvpOSName": "Windows Server 2022 Standard",
        "KvpOSVersion": "10.0.20348",
        "KvpFQDN": "hv-vm01.example.invalid",
        "KvpIPv4": "10.0.0.11;169.254.1.1",
        "KvpIPv6": None,
        "Adapters": [{"IPAddresses": ["10.0.0.11", "fe80::1"]}],
    }
    row.update(overrides)
    return row


class TestHostGuestInfo:
    def test_no_kvp_means_tools_not_installed(self) -> None:
        guest = host_mapper.map_guest_info(_host_row(IntegrationOk=False), NOW)
        assert guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED
        assert guest.os_name is None

    def test_kvp_present_but_empty_means_not_running(self) -> None:
        """부팅 직후 등 — KVP 교환은 되는데 유의미한 값이 없다 (§8.2)."""
        guest = host_mapper.map_guest_info(
            _host_row(KvpOSName=None, KvpFQDN=None, KvpIPv4=None, Adapters=[]),
            NOW,
        )
        assert guest.availability is GuestInfoAvailability.TOOLS_NOT_RUNNING

    def test_full_kvp_maps_to_available_with_guest_tools_source(self) -> None:
        guest = host_mapper.map_guest_info(_host_row(), NOW)
        assert guest.availability is GuestInfoAvailability.AVAILABLE
        assert guest.os_name == "Windows Server 2022 Standard"
        assert guest.os_source is OsSource.GUEST_TOOLS
        assert guest.hostname == "hv-vm01.example.invalid"
        assert guest.observed_at == NOW

    def test_link_local_addresses_are_filtered(self) -> None:
        guest = host_mapper.map_guest_info(_host_row(), NOW)
        assert guest.ipv4_addresses == ("10.0.0.11",)  # 169.254는 제외, 중복 병합
        assert guest.ipv6_addresses == ()  # fe80은 제외

    def test_adapter_ips_fill_in_when_kvp_ip_is_empty(self) -> None:
        guest = host_mapper.map_guest_info(
            _host_row(KvpIPv4=None, KvpOSName=None, KvpFQDN=None), NOW
        )
        assert guest.availability is GuestInfoAvailability.AVAILABLE
        assert guest.ipv4_addresses == ("10.0.0.11",)


class TestHostVmMapping:
    def test_basic_fields(self) -> None:
        vm = host_mapper.map_virtual_machine(CONN, _host_row(), NOW)
        assert vm.native_id == "a1b2c3d4-0000-1111-2222-333344445555"
        assert vm.name == "hv-vm01"
        assert vm.power_state is PowerState.ON
        assert vm.cpu.total_vcpu == 4
        assert vm.host_native_id == "HV-HOST01"
        assert vm.last_seen_at == NOW

    def test_bios_guid_is_normalized_for_cross_identification(self) -> None:
        vm = host_mapper.map_virtual_machine(CONN, _host_row(), NOW)
        assert vm.bios_uuid == "4c4c4544-004d-3510-8054-b4c04f435831"

    def test_memory_bytes_become_mb_with_startup_fallback(self) -> None:
        vm = host_mapper.map_virtual_machine(CONN, _host_row(), NOW)
        assert vm.memory.assigned_mb == 8192
        # 꺼진 VM은 MemoryAssigned가 0이라 MemoryStartup으로 대체한다
        off = host_mapper.map_virtual_machine(CONN, _host_row(MemoryAssigned=0), NOW)
        assert off.memory.assigned_mb == 4096

    def test_generation_maps_to_firmware(self) -> None:
        assert (
            host_mapper.map_virtual_machine(CONN, _host_row(Generation=2), NOW).platform.firmware
            is Firmware.UEFI
        )
        assert (
            host_mapper.map_virtual_machine(CONN, _host_row(Generation=1), NOW).platform.firmware
            is Firmware.BIOS
        )

    def test_hyperv_has_no_configured_os(self) -> None:
        """경로 A에는 VM 구성값 OS가 없다 — KVP가 유일한 출처다 (§8.2)."""
        vm = host_mapper.map_virtual_machine(CONN, _host_row(), NOW)
        assert vm.platform.configured_os is None


def _scvmm_row(**overrides: object) -> dict:
    row = {
        "Id": "a1b2c3d4-0000-1111-2222-333344445555",
        "Name": "hv-vm01",
        "State": "Running",
        "Generation": 1,
        "Version": "10.0",
        "ProcessorCount": 2,
        "MemoryMB": 4096,
        "OperatingSystem": "Windows Server 2022 Standard",
        "HostName": "hv-host01.example.invalid",
        "Adapters": [{"IPv4": ["10.0.0.11"], "IPv6": []}],
    }
    row.update(overrides)
    return row


class TestScvmmGuestInfo:
    def test_os_source_is_vm_config(self) -> None:
        """VMM DB 값은 생성 시 지정값일 수 있다 — 확인 전까지 구성값 취급 (§8.5)."""
        guest = scvmm_mapper.map_scvmm_guest(_scvmm_row(), NOW)
        assert guest.availability is GuestInfoAvailability.AVAILABLE
        assert guest.os_source is OsSource.VM_CONFIG

    def test_no_ip_and_no_os_means_tools_not_installed(self) -> None:
        guest = scvmm_mapper.map_scvmm_guest(
            _scvmm_row(OperatingSystem=None, Adapters=[]), NOW
        )
        assert guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED


class TestScvmmVmMapping:
    def test_native_id_is_vm_guid_not_vmm_object_id(self) -> None:
        """native_id는 VMId다 — 경로 A와 같은 VM에 같은 식별자를 준다 (§8.4)."""
        vm = scvmm_mapper.map_scvmm_vm(CONN, _scvmm_row(), NOW)
        assert vm.native_id == "a1b2c3d4-0000-1111-2222-333344445555"

    def test_memory_is_already_mb(self) -> None:
        vm = scvmm_mapper.map_scvmm_vm(CONN, _scvmm_row(), NOW)
        assert vm.memory.assigned_mb == 4096

    def test_configured_os_holds_vmm_value(self) -> None:
        vm = scvmm_mapper.map_scvmm_vm(CONN, _scvmm_row(), NOW)
        assert vm.platform.configured_os == "Windows Server 2022 Standard"

    def test_power_state_from_enum_name(self) -> None:
        assert (
            scvmm_mapper.map_scvmm_vm(CONN, _scvmm_row(State="Saved"), NOW).power_state
            is PowerState.SUSPENDED
        )
