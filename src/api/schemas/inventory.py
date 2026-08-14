"""자원 응답 스키마 (계획 08 §6.3).

게스트 정보는 평면 필드가 아니라 **중첩 객체**로 내려보낸다. `unavailable_reason`을
**서버가 채우므로** UI는 분기 없이 그대로 출력하면 되고, 표현 문구가 여러 화면에
흩어지지 않는다 (ROADMAP §9.2·§11.1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.domain.enums import (
    GuestInfoAvailability,
    HypervisorKind,
    OsSource,
    PowerState,
    ResourceLifecycle,
)
from src.domain.query import VmSummary
from src.domain.values import unavailable_reason


class GuestInfoResponse(BaseModel):
    availability: GuestInfoAvailability
    is_collected: bool
    #: UNAVAILABLE_REASONS 매핑 (계획 02 §5.1). 수집된 상태면 None.
    unavailable_reason: str | None
    os_name: str | None
    os_source: OsSource | None
    hostname: str | None
    #: 마지막 확인 시각. 폴백된 값이면 원래 관측 시각이 남아 있다 (ROADMAP §7.4)
    observed_at: datetime | None
    # ipv4_addresses / ipv6_addresses는 Step 4에서 이 객체에 추가된다


class VmSummaryResponse(BaseModel):
    resource_id: UUID
    name: str
    native_id: str
    connection_id: UUID
    connection_name: str
    hypervisor: HypervisorKind
    power_state: PowerState
    vcpu_count: int | None
    memory_mb: int | None
    guest: GuestInfoResponse
    host_native_id: str | None
    lifecycle: ResourceLifecycle
    last_seen_at: datetime

    @classmethod
    def from_summary(cls, vm: VmSummary) -> "VmSummaryResponse":
        collected = vm.guest_availability is GuestInfoAvailability.AVAILABLE
        return cls(
            resource_id=vm.resource_id,
            name=vm.name,
            native_id=vm.native_id,
            connection_id=vm.connection_id,
            connection_name=vm.connection_name,
            hypervisor=vm.hypervisor,
            power_state=vm.power_state,
            vcpu_count=vm.vcpu_count,
            memory_mb=vm.memory_mb,
            guest=GuestInfoResponse(
                availability=vm.guest_availability,
                is_collected=collected,
                unavailable_reason=None if collected else unavailable_reason(vm.guest_availability),
                # 수집 불가여도 폴백으로 남은 이전 값을 함께 내려보낸다.
                # UI가 "수집 불가 — 사유 (마지막 확인: …)"로 표시한다 (ROADMAP §11.1).
                os_name=vm.guest_os_name,
                os_source=vm.guest_os_source,
                hostname=vm.guest_hostname,
                observed_at=vm.guest_observed_at,
            ),
            host_native_id=vm.host_native_id,
            lifecycle=vm.lifecycle,
            last_seen_at=vm.last_seen_at,
        )
