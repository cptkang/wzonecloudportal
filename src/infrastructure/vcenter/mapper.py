"""pyVmomi 속성 dict → 도메인 모델 (계획 04 §6.1·6.2).

장치 매핑(§6.3)은 Step 4에서 `config.hardware.device` 수집과 함께 추가한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from src.domain.enums import ConnectionState, GuestInfoAvailability, PowerState
from src.domain.resource import VirtualMachine
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec, resolve_os_name

VCENTER_POWER_MAP: dict[str, PowerState] = {
    "poweredOn": PowerState.ON,
    "poweredOff": PowerState.OFF,
    "suspended": PowerState.SUSPENDED,
}

VCENTER_CONNECTION_STATE_MAP: dict[str, ConnectionState] = {
    "connected": ConnectionState.CONNECTED,
    "disconnected": ConnectionState.DISCONNECTED,
    "inaccessible": ConnectionState.INACCESSIBLE,
    "invalid": ConnectionState.INACCESSIBLE,
    "orphaned": ConnectionState.ORPHANED,
}


def map_virtual_machine(
    connection_id: UUID, moid: str, props: dict[str, Any], observed_at: datetime
) -> VirtualMachine:
    return VirtualMachine(
        # 저장소가 식별키로 기존 자원을 찾으면 이 값 대신 기존 resource_id를 쓴다
        resource_id=uuid4(),
        connection_id=connection_id,
        # instanceUuid가 없는 VM이 있을 수 있어 MoRef로 대체한다 (Step 2 §15.2-11에서 실측)
        native_id=props.get("config.instanceUuid") or moid,
        name=props.get("name") or moid,
        bios_uuid=props.get("config.uuid"),
        power_state=VCENTER_POWER_MAP.get(str(props.get("runtime.powerState")), PowerState.UNKNOWN),
        connection_state=VCENTER_CONNECTION_STATE_MAP.get(
            str(props.get("runtime.connectionState")), ConnectionState.UNKNOWN
        ),
        cpu=CpuSpec(total_vcpu=props.get("config.hardware.numCPU") or 0),
        memory=MemorySpec(assigned_mb=props.get("config.hardware.memoryMB") or 0),
        platform=PlatformSpec(configured_os=props.get("config.guestFullName")),
        guest=map_guest_info(props, observed_at),
        host_native_id=moref_or_none(props.get("runtime.host")),
        last_seen_at=observed_at,
    )


def map_guest_info(props: dict[str, Any], observed_at: datetime) -> GuestInfo:
    """게스트 정보 매핑 (FR-501) — 이 매퍼에서 가장 조심할 지점.

    Tools 상태 보고가 부정확한 알려진 버그가 있으므로 **상태만 믿지 말고 값 유무도 함께 본다**
    (`docs/00_research_notes.md` §6.3).
    """
    tools_status = props.get("guest.toolsStatus")
    running = props.get("guest.toolsRunningStatus")

    if tools_status == "toolsNotInstalled":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    if running is not None and running != "guestToolsRunning":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    hostname = props.get("guest.hostName")
    tools_os = props.get("guest.guestFullName")

    if not hostname and not tools_os:
        # 상태는 정상인데 값이 없다 — 부팅 직후일 수 있다.
        # 빈 값으로 두지 않고 UNKNOWN으로 남겨 "확인 필요"로 표시한다.
        return GuestInfo(availability=GuestInfoAvailability.UNKNOWN)

    os_name, os_source = resolve_os_name(tools_os, props.get("config.guestFullName"))
    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_source=os_source,
        hostname=hostname,
        observed_at=observed_at,
    )


def moref_or_none(value: Any) -> str | None:
    """ManagedObjectReference를 문자열 ID로 바꾼다.

    Step 6까지는 이 MoRef를 그대로 표시하고, 그때 실제 호스트명으로 해석한다.
    """
    if value is None:
        return None
    moid = getattr(value, "_moId", None)
    return str(moid) if moid else str(value)
