"""자원 엔티티 (계획 02 §6). Step 1은 VirtualMachine만 정의한다.

Host/Cluster/Datastore/Network는 Step 6, 디스크·어댑터·스냅샷은 Step 4에서 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from src.domain.enums import (
    ConnectionState,
    GuestInfoAvailability,
    PowerState,
    ResourceLifecycle,
)
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec


@dataclass
class ResourceBase:
    resource_id: UUID
    #: 소속 연결 (불변 — FR-110)
    connection_id: UUID
    #: 하이퍼바이저 고유 ID
    native_id: str
    name: str
    lifecycle: ResourceLifecycle = ResourceLifecycle.ACTIVE
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    def is_stale(self, threshold: timedelta, now: datetime) -> bool:
        """데이터 신선도 판정 (FR-502)."""
        if self.last_seen_at is None:
            return True
        return (now - self.last_seen_at) > threshold


@dataclass
class VirtualMachine(ResourceBase):
    bios_uuid: str | None = None
    power_state: PowerState = PowerState.UNKNOWN
    connection_state: ConnectionState = ConnectionState.UNKNOWN

    cpu: CpuSpec = field(default_factory=lambda: CpuSpec(total_vcpu=0))
    memory: MemorySpec = field(default_factory=lambda: MemorySpec(assigned_mb=0))
    platform: PlatformSpec = field(default_factory=PlatformSpec)
    guest: GuestInfo = field(
        default_factory=lambda: GuestInfo(availability=GuestInfoAvailability.UNKNOWN)
    )

    #: 관계는 native_id 문자열 참조다 (FK 아님 — 계획 06 §5.1).
    #: Step 6에서 실제 호스트명으로 해석한다.
    host_native_id: str | None = None

    @property
    def mac_addresses(self) -> tuple[str, ...]:
        """Step 4에서 어댑터를 수집하면 채워진다. 지금은 항상 빈 튜플이다.

        CI 식별 3순위(MAC+이름) 키 생성이 이 값을 쓴다 (계획 02 §7).
        """
        return ()
