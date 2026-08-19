"""목 커넥터 (계획 03 §8).

실제 하이퍼바이저에 연결하지 않으므로(CST-04) 목 구현이 개발·테스트의 기반이다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.domain.connection import Connection
from src.domain.enums import (
    CheckStage,
    ConnectionState,
    GuestInfoAvailability,
    OsSource,
    PowerState,
    ResourceType,
)
from src.domain.exceptions import AuthenticationError, UnreachableError
from src.domain.ports import CollectionOutcome, ConnectionCheckResult, StageResult
from src.domain.resource import VirtualMachine
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec


class FakeInventoryReader:
    """시나리오를 주입하여 다양한 상황을 재현한다."""

    def __init__(
        self,
        connection: Connection,
        *,
        vms: Sequence[VirtualMachine] = (),
        fail_types: set[ResourceType] | None = None,
        auth_error: bool = False,
        unreachable: bool = False,
    ) -> None:
        self._conn = connection
        self._vms = list(vms)
        self._fail_types = fail_types or set()
        self._auth_error = auth_error
        self._unreachable = unreachable
        self._outcomes: list[CollectionOutcome] = []
        self.session_open = False
        self.start_calls = 0

    @property
    def connection_id(self) -> UUID:
        return self._conn.connection_id

    @property
    def is_session_closed(self) -> bool:
        return not self.session_open

    async def start_session(self) -> None:
        self.start_calls += 1
        if self._auth_error:
            raise AuthenticationError("인증에 실패했습니다.")
        if self._unreachable:
            raise UnreachableError("서버에 연결할 수 없습니다.")
        self.session_open = True

    async def close_session(self) -> None:
        self.session_open = False

    async def __aenter__(self) -> FakeInventoryReader:
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()

    async def check_connection(self) -> ConnectionCheckResult:
        failed = self._auth_error or self._unreachable
        stages = tuple(
            StageResult(stage=stage, passed=not failed, skipped=False)
            for stage in CheckStage
        )
        return ConnectionCheckResult(
            stages=stages,
            readable_types=frozenset() if failed else frozenset({ResourceType.VIRTUAL_MACHINE}),
            server_version=None if failed else "Fake vCenter 8.0.2",
        )

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        if ResourceType.VIRTUAL_MACHINE in self._fail_types:
            # 한 유형의 실패가 예외로 전파되면 안 된다 (FR-204)
            self._outcomes.append(
                CollectionOutcome(
                    resource_type=ResourceType.VIRTUAL_MACHINE,
                    collected_count=0,
                    failed=True,
                    error="시뮬레이션 실패",
                )
            )
            return
        count = 0
        for vm in self._vms:
            count += 1
            yield vm
        self._outcomes.append(
            CollectionOutcome(
                resource_type=ResourceType.VIRTUAL_MACHINE, collected_count=count, failed=False
            )
        )

    def get_outcomes(self) -> Sequence[CollectionOutcome]:
        return tuple(self._outcomes)


def make_vm(
    *,
    connection_id: UUID,
    name: str = "test-vm",
    native_id: str | None = None,
    bios_uuid: str | None = None,
    power: PowerState = PowerState.ON,
    vcpu: int = 2,
    memory_mb: int = 4096,
    guest_available: bool = True,
    guest_availability: GuestInfoAvailability | None = None,
    os_name: str | None = "Ubuntu 22.04 LTS",
    os_source: OsSource = OsSource.GUEST_TOOLS,
    hostname: str | None = "test-vm.example.invalid",
    configured_os: str | None = "Ubuntu Linux (64-bit)",
    host_native_id: str | None = "host-1001",
    observed_at: datetime | None = None,
) -> VirtualMachine:
    """테스트용 VM. 필요한 필드만 지정하고 나머지는 기본값을 쓴다 (계획 03 §8.1).

    목 데이터에 실제 서버명·IP를 쓰지 않는다 (NFR-206). `.invalid`는 예약 TLD다.
    """
    at = observed_at or datetime.now(UTC)
    availability = guest_availability or (
        GuestInfoAvailability.AVAILABLE if guest_available else GuestInfoAvailability.TOOLS_NOT_INSTALLED
    )
    collected = availability is GuestInfoAvailability.AVAILABLE
    return VirtualMachine(
        resource_id=uuid4(),
        connection_id=connection_id,
        native_id=native_id or f"instance-{name}",
        name=name,
        bios_uuid=bios_uuid,
        power_state=power,
        connection_state=ConnectionState.CONNECTED,
        cpu=CpuSpec(total_vcpu=vcpu),
        memory=MemorySpec(assigned_mb=memory_mb),
        platform=PlatformSpec(configured_os=configured_os),
        guest=GuestInfo(
            availability=availability,
            os_name=os_name if collected else None,
            os_source=os_source if collected else None,
            hostname=hostname if collected else None,
            observed_at=at if collected else None,
        ),
        host_native_id=host_native_id,
        last_seen_at=at,
    )
