"""SCVMM 응답 → 도메인 모델 (계획 05 §8.4~8.6의 MVP 축소판).

- `native_id`는 **VMId(Hyper-V VM GUID)**다. VMM 객체 ID는 SCVMM 재설치·재등록 시
  바뀔 수 있고, 경로 A와 같은 VM에 같은 식별자를 주어야 연결 유형 전환 시 중복 후보
  감지가 동작한다 (§8.4). VMId가 없는 행은 **호출자(리더)가 제외**한다.
- SCVMM의 게스트 값은 KVP 실시간이 아니라 **VMM DB 캐시**다. OS는 VM 생성 시 지정값일 수
  있으므로 실환경 확인 전까지 구성값(`OsSource.VM_CONFIG`)으로 판정한다 (§8.5).
- VMM `Owner`는 수집하지 않는다 — 포탈 소유자 메타데이터(FR-601)와 혼합 금지 (§8.6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from src.domain.enums import ConnectionState, Firmware, GuestInfoAvailability, OsSource
from src.domain.resource import VirtualMachine
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec
from src.infrastructure.hyperv.normalize import map_power_state
from src.utils.net import split_ip_families


def map_scvmm_guest(row: dict[str, Any], observed_at: datetime) -> GuestInfo:
    """게스트 정보 판정 (FR-501, 계획 05 §8.5).

    IP도 OS도 없으면 통합 서비스가 동작하지 않는 것으로 본다.
    """
    v4, v6 = split_ip_families(
        [
            ip
            for ad in row.get("Adapters") or []
            for ip in (ad.get("IPv4") or []) + (ad.get("IPv6") or [])
        ]
    )
    os_name = row.get("OperatingSystem") or None

    if not v4 and not v6 and not os_name:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        #: VMM DB 값 — 갱신 시점이 확인되기 전까지 구성값 취급 (§8.5 [검증 필요])
        os_source=OsSource.VM_CONFIG if os_name else None,
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        observed_at=observed_at,
    )


def map_scvmm_vm(connection_id: UUID, row: dict[str, Any], observed_at: datetime) -> VirtualMachine:
    """VMId가 있는 행만 넘겨야 한다 — 식별자 없는 자원은 재수집마다 중복이 쌓인다 (§8.4)."""
    native_id = str(row["Id"])
    generation = row.get("Generation")
    return VirtualMachine(
        resource_id=uuid4(),
        connection_id=connection_id,
        native_id=native_id,
        name=row.get("Name") or native_id,
        # SCVMM 경로는 BIOS GUID를 주지 않는다 (계획 05 §7.2). 교차 식별은 native_id로 충분하다.
        bios_uuid=None,
        power_state=map_power_state(row.get("State")),
        connection_state=ConnectionState.CONNECTED,
        cpu=CpuSpec(total_vcpu=int(row.get("ProcessorCount") or 0)),
        # SCVMM의 Memory는 이미 MB 단위다 (경로 A의 바이트와 다르다)
        memory=MemorySpec(assigned_mb=int(row.get("MemoryMB") or 0)),
        platform=PlatformSpec(
            hardware_version=row.get("Version"),
            firmware=(
                Firmware.UEFI
                if generation == 2
                else Firmware.BIOS if generation == 1 else Firmware.UNKNOWN
            ),
            configured_os=row.get("OperatingSystem") or None,
        ),
        guest=map_scvmm_guest(row, observed_at),
        host_native_id=row.get("HostName") or None,
        last_seen_at=observed_at,
    )
