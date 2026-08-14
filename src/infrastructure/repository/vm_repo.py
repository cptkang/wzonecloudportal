"""VM 저장소 — CI 식별 규칙 적용 upsert와 범위 필터 조회 (계획 06 §3, ROADMAP §7.3·7.4).

**`INSERT ... ON CONFLICT DO UPDATE`로 만들지 않는다.** 가장 짧게 쓰는 방법이지만,
2·3순위 식별(BIOS UUID, MAC+이름)을 추가하는 순간 "제약 위반을 잡아 갱신"에서
"식별키로 찾아 분기"로 알고리즘 자체가 바뀐다. Step 4·5에서 upsert 전면 재작성이다.

Step 4·5에서 늘어나는 것은 `build_vm_identity_keys`가 반환하는 **행의 수뿐**이다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.auth import AccessScope
from src.domain.enums import (
    ConnectionState,
    GuestInfoAvailability,
    HypervisorKind,
    OsSource,
    PowerState,
    ResourceLifecycle,
    ResourceType,
)
from src.domain.identity import IdentityKey, IdentityRule, build_vm_identity_keys
from src.domain.query import Page, PagedResult, VmSummary
from src.domain.repositories import UpsertResult
from src.domain.resource import VirtualMachine
from src.domain.values import CpuSpec, GuestInfo, MemorySpec, PlatformSpec
from src.infrastructure.db.models import ConnectionRow, ResourceIdentityRow, VirtualMachineRow

logger = logging.getLogger(__name__)

#: 변경 여부 판정에 쓰는 필드. 변경 이력(계획 12)은 Step 8이므로 여기서는 건수만 센다.
_TRACKED_COLUMNS = (
    "name",
    "power_state",
    "connection_state",
    "vcpu_count",
    "memory_mb",
    "configured_os",
    "guest_availability",
    "guest_os_name",
    "guest_os_source",
    "guest_hostname",
    "host_native_id",
    "bios_uuid",
)

_SORT_COLUMNS = {
    "name": VirtualMachineRow.name,
    "power_state": VirtualMachineRow.power_state,
    "guest_os_name": VirtualMachineRow.guest_os_name,
    "vcpu_count": VirtualMachineRow.vcpu_count,
    "memory_mb": VirtualMachineRow.memory_mb,
    "last_seen_at": VirtualMachineRow.last_seen_at,
}


@dataclass(frozen=True, slots=True)
class IdentityMatch:
    resource_id: UUID
    connection_id: UUID
    rule: IdentityRule
    value: str


class VirtualMachineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 수집 경로 ────────────────────────────────────────────

    async def upsert_virtual_machines(
        self, connection_id: UUID, vms: Sequence[VirtualMachine], observed_at: datetime
    ) -> UpsertResult:
        created = updated = unchanged = 0

        for vm in vms:
            keys = build_vm_identity_keys(vm)
            match = await self._find_by_identity(keys)

            if match is None:
                resource_id = await self._insert_vm(vm, observed_at)
                created += 1
            else:
                resource_id = match.resource_id
                previous = await self._load_vm(resource_id)
                merged = _merge_guest(vm, previous)
                changed = await self._update_vm(resource_id, merged, observed_at)
                if changed:
                    updated += 1
                else:
                    unchanged += 1
            await self._sync_identities(resource_id, keys, connection_id)

        await self._session.flush()
        return UpsertResult(created=created, updated=updated, unchanged=unchanged)

    async def _find_by_identity(self, keys: Sequence[IdentityKey]) -> IdentityMatch | None:
        """우선순위 순으로 조회한다.

        `limit(2)`로 모호성을 감지한다 — 같은 BIOS UUID를 가진 클론이 여럿이면
        임의로 하나를 고르면 안 되고 다음 순위로 넘어가야 한다.
        """
        for key in sorted(keys, key=lambda k: k.rule):
            rows = (
                await self._session.execute(
                    select(ResourceIdentityRow.resource_id, ResourceIdentityRow.connection_id)
                    .where(
                        ResourceIdentityRow.rule == int(key.rule),
                        ResourceIdentityRow.key_value == key.value,
                    )
                    .limit(2)
                )
            ).all()
            if len(rows) == 1:
                return IdentityMatch(
                    resource_id=rows[0][0], connection_id=rows[0][1], rule=key.rule, value=key.value
                )
            if len(rows) > 1:
                logger.warning(
                    "식별 키 모호 — 다음 우선순위로 진행",
                    extra={"rule": int(key.rule), "count": len(rows)},
                )
        return None

    async def _insert_vm(self, vm: VirtualMachine, observed_at: datetime) -> UUID:
        resource_id = uuid4()
        self._session.add(
            VirtualMachineRow(
                resource_id=resource_id,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                lifecycle=ResourceLifecycle.ACTIVE.value,
                **_to_columns(vm),
            )
        )
        await self._session.flush()
        return resource_id

    async def _load_vm(self, resource_id: UUID) -> VirtualMachine | None:
        row = await self._session.get(VirtualMachineRow, resource_id)
        return _row_to_domain(row) if row is not None else None

    async def _update_vm(
        self, resource_id: UUID, vm: VirtualMachine, observed_at: datetime
    ) -> bool:
        row = await self._session.get(VirtualMachineRow, resource_id)
        if row is None:
            return False
        columns = _to_columns(vm)
        changed = any(getattr(row, name) != columns[name] for name in _TRACKED_COLUMNS)
        for name, value in columns.items():
            setattr(row, name, value)
        row.last_seen_at = observed_at
        await self._session.flush()
        return changed

    async def _sync_identities(
        self, resource_id: UUID, keys: Sequence[IdentityKey], connection_id: UUID
    ) -> None:
        """이 자원의 식별 행을 현재 키 집합과 일치시킨다.

        Step 4·5에서 키가 늘거나(어댑터 추가) 줄면(MAC 변경) 여기서 자동 반영된다.
        """
        await self._session.execute(
            delete(ResourceIdentityRow).where(ResourceIdentityRow.resource_id == resource_id)
        )
        for key in keys:
            self._session.add(
                ResourceIdentityRow(
                    resource_id=resource_id,
                    resource_type=ResourceType.VIRTUAL_MACHINE.value,
                    connection_id=connection_id,
                    rule=int(key.rule),
                    key_value=key.value,
                )
            )
        await self._session.flush()

    # ── 조회 경로 ────────────────────────────────────────────

    async def search_vms(
        self, scope: AccessScope, connection_id: UUID | None, page: Page
    ) -> PagedResult[VmSummary]:
        page.validate()

        # 범위가 비면 DB를 조회하지 않는다 (계획 09 §3.3). 기본 거부가 자연스럽게 성립한다.
        if scope.is_empty:
            return PagedResult(items=(), total=0, page=page)

        base = (
            select(VirtualMachineRow, ConnectionRow.display_name, ConnectionRow.kind)
            .join(ConnectionRow, ConnectionRow.connection_id == VirtualMachineRow.connection_id)
            .where(VirtualMachineRow.lifecycle == ResourceLifecycle.ACTIVE.value)
        )
        base = _apply_scope(base, scope)
        if connection_id is not None:
            base = base.where(VirtualMachineRow.connection_id == connection_id)

        total = await self._session.scalar(
            select(func.count()).select_from(base.subquery())
        )

        column = _SORT_COLUMNS[page.sort_by]
        order = column.desc() if page.sort_desc else column.asc()
        # 정렬 컬럼에 동일 값이 많으면 페이지 경계에서 행이 중복·누락된다.
        # 고유 컬럼을 타이브레이커로 반드시 붙인다 (계획 07 §3.2).
        rows = (
            await self._session.execute(
                base.order_by(order, VirtualMachineRow.resource_id)
                .offset(page.offset)
                .limit(page.limit)
            )
        ).all()

        items = tuple(_row_to_summary(vm, name, kind) for vm, name, kind in rows)
        return PagedResult(items=items, total=int(total or 0), page=page)

    async def count_active_vms(self, connection_id: UUID) -> int:
        """연결 삭제 영향 판정용 (FR-109). 사용자 조회가 아니므로 범위를 받지 않는다."""
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(VirtualMachineRow)
                .where(
                    VirtualMachineRow.connection_id == connection_id,
                    VirtualMachineRow.lifecycle == ResourceLifecycle.ACTIVE.value,
                )
            )
            or 0
        )

    async def count_by_connection(self, scope: AccessScope) -> dict[UUID, int]:
        """연결별 활성 VM 수. 연결 관리 화면의 건수 컬럼에 쓴다."""
        if scope.is_empty:
            return {}
        stmt = (
            select(VirtualMachineRow.connection_id, func.count())
            .where(VirtualMachineRow.lifecycle == ResourceLifecycle.ACTIVE.value)
            .group_by(VirtualMachineRow.connection_id)
        )
        stmt = _apply_scope(stmt, scope)
        return {row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()}


def _apply_scope(stmt: Select, scope: AccessScope) -> Select:  # type: ignore[type-arg]
    """조회 범위를 SQL에 반영한다 (FR-1003).

    조회 후 파이썬에서 거르면 페이징 건수가 어긋나고 누락된다 (계획 09 §3.1).
    """
    if scope.is_unrestricted:
        return stmt
    return stmt.where(
        VirtualMachineRow.connection_id.in_(list(scope.allowed_connection_ids or frozenset()))
    )


def _merge_guest(incoming: VirtualMachine, previous: VirtualMachine | None) -> VirtualMachine:
    """도구가 미동작이면 이전 게스트 값을 유지한다 (계획 06 §3.3, ROADMAP §7.4).

    도구가 멈춘 것이지 VM의 OS가 사라진 것이 아니다. 그대로 저장하면 직전 수집에서 얻은
    게스트 OS·호스트명이 NULL로 덮어써지고 **복구할 수 없다.**
    """
    if previous is None:
        return incoming
    return replace(incoming, guest=incoming.guest.with_fallback(previous.guest))


def _to_columns(vm: VirtualMachine) -> dict[str, object]:
    return {
        "connection_id": vm.connection_id,
        "native_id": vm.native_id,
        "bios_uuid": vm.bios_uuid,
        "name": vm.name,
        "power_state": vm.power_state.value,
        "connection_state": vm.connection_state.value,
        "vcpu_count": vm.cpu.total_vcpu or None,
        "memory_mb": vm.memory.assigned_mb or None,
        "configured_os": vm.platform.configured_os,
        "guest_availability": vm.guest.availability.value,
        "guest_os_name": vm.guest.os_name,
        "guest_os_source": vm.guest.os_source.value if vm.guest.os_source else None,
        "guest_hostname": vm.guest.hostname,
        "guest_observed_at": vm.guest.observed_at,
        "host_native_id": vm.host_native_id,
    }


def _row_to_domain(row: VirtualMachineRow) -> VirtualMachine:
    return VirtualMachine(
        resource_id=row.resource_id,
        connection_id=row.connection_id,
        native_id=row.native_id,
        name=row.name,
        lifecycle=ResourceLifecycle(row.lifecycle),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        bios_uuid=row.bios_uuid,
        power_state=PowerState(row.power_state),
        connection_state=ConnectionState(row.connection_state),
        cpu=CpuSpec(total_vcpu=row.vcpu_count or 0),
        memory=MemorySpec(assigned_mb=row.memory_mb or 0),
        platform=PlatformSpec(configured_os=row.configured_os),
        guest=GuestInfo(
            availability=GuestInfoAvailability(row.guest_availability),
            os_name=row.guest_os_name,
            os_source=OsSource(row.guest_os_source) if row.guest_os_source else None,
            hostname=row.guest_hostname,
            observed_at=row.guest_observed_at,
        ),
        host_native_id=row.host_native_id,
    )


def _row_to_summary(row: VirtualMachineRow, connection_name: str, kind: str) -> VmSummary:
    return VmSummary(
        resource_id=row.resource_id,
        name=row.name,
        native_id=row.native_id,
        connection_id=row.connection_id,
        connection_name=connection_name,
        hypervisor=HypervisorKind.VCENTER if kind == "vcenter" else HypervisorKind.HYPERV,
        power_state=PowerState(row.power_state),
        vcpu_count=row.vcpu_count,
        memory_mb=row.memory_mb,
        configured_os=row.configured_os,
        guest_availability=GuestInfoAvailability(row.guest_availability),
        guest_os_name=row.guest_os_name,
        guest_os_source=OsSource(row.guest_os_source) if row.guest_os_source else None,
        guest_hostname=row.guest_hostname,
        guest_observed_at=row.guest_observed_at,
        host_native_id=row.host_native_id,
        lifecycle=ResourceLifecycle(row.lifecycle),
        last_seen_at=row.last_seen_at,
    )
