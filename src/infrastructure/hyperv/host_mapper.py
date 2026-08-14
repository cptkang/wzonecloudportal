"""Hyper-V 호스트 응답 → 도메인 모델 — 경로 A (계획 05 §8.2·§8.3의 MVP 축소판).

**경로 A의 Hyper-V에는 VM 구성값 OS가 없다.** 게스트 OS는 KVP가 유일한 출처이므로
KVP가 없으면 OS를 알 수 없다 (vCenter `config.guestFullName` 같은 대체가 없음).
따라서 `configured_os`는 항상 None이고, os_source는 항상 GUEST_TOOLS다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from src.domain.enums import ConnectionState, Firmware, GuestInfoAvailability, OsSource
from src.domain.resource import VirtualMachine
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec
from src.infrastructure.hyperv.normalize import (
    map_power_state,
    normalize_guid,
    split_kvp_addresses,
    to_mb,
)
from src.utils.net import split_ip_families


def map_guest_info(row: dict[str, Any], observed_at: datetime) -> GuestInfo:
    """KVP 직접 판정 (FR-501, 계획 05 §8.2).

    - KVP 항목이 하나도 없음 → 통합 서비스(KVP 교환) 미동작 = TOOLS_NOT_INSTALLED
    - KVP는 있는데 유의미한 값이 없음 → TOOLS_NOT_RUNNING (부팅 직후 등)
    """
    if not row.get("IntegrationOk"):
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    v4_raw = split_kvp_addresses(row.get("KvpIPv4"))
    v6_raw = split_kvp_addresses(row.get("KvpIPv6"))
    # 어댑터에서 얻은 IP도 병합한다 — KVP가 비어도 Get-VMNetworkAdapter가 줄 수 있다
    for ad in row.get("Adapters") or []:
        v4_raw.extend(ad.get("IPAddresses") or [])

    v4, v6 = split_ip_families(v4_raw + v6_raw)
    fqdn = row.get("KvpFQDN") or None
    os_name = row.get("KvpOSName") or None

    if not fqdn and not os_name and not v4 and not v6:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_version=row.get("KvpOSVersion") or None,
        os_source=OsSource.GUEST_TOOLS if os_name else None,
        hostname=fqdn,
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        observed_at=observed_at,
    )


def map_virtual_machine(
    connection_id: UUID, row: dict[str, Any], observed_at: datetime
) -> VirtualMachine:
    native_id = str(row["Id"])
    generation = row.get("Generation")
    return VirtualMachine(
        resource_id=uuid4(),
        connection_id=connection_id,
        native_id=native_id,  # Hyper-V VM GUID — 경로 B의 VMId와 동일한 값이다 (§8.4)
        name=row.get("Name") or native_id,
        bios_uuid=normalize_guid(row.get("BiosGuid")),
        power_state=map_power_state(row.get("State")),
        connection_state=ConnectionState.CONNECTED,
        cpu=CpuSpec(total_vcpu=int(row.get("ProcessorCount") or 0)),
        # MemoryAssigned는 실행 중에만 값이 있다. 꺼진 VM은 MemoryStartup으로 대체한다
        memory=MemorySpec(
            assigned_mb=to_mb(row.get("MemoryAssigned") or row.get("MemoryStartup")) or 0
        ),
        platform=PlatformSpec(
            hardware_version=row.get("Version"),
            firmware=(
                Firmware.UEFI
                if generation == 2
                else Firmware.BIOS if generation == 1 else Firmware.UNKNOWN
            ),
            configured_os=None,  # Hyper-V는 구성값 OS가 없다 (§8.2)
        ),
        guest=map_guest_info(row, observed_at),
        host_native_id=row.get("ComputerName") or None,
        last_seen_at=observed_at,
    )
