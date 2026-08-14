"""저장소 Protocol (계획 03 §6, 06 §3.4).

수집 경로용 쓰기 저장소와 조회용 저장소를 분리한다. 수집 경로에 메타데이터 쓰기
메서드를 노출하지 않는 것이 FR-602의 구조적 보장이다 (Step 7에서 실효).

**모든 조회 메서드는 `AccessScope`를 첫 필수 인자로 받는다** (계획 09 §3.1).
선택 인자로 두면 빠뜨리기 쉽고, 누락된 경로 하나가 권한 우회가 된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.auth import AccessScope
from src.domain.query import Page, PagedResult, VmSummary
from src.domain.resource import VirtualMachine


@dataclass(frozen=True, slots=True)
class UpsertResult:
    created: int
    updated: int
    unchanged: int


class InventoryWriteRepository(Protocol):
    """수집 경로 전용. 메타데이터 쓰기 메서드가 없다."""

    async def upsert_virtual_machines(
        self, connection_id: UUID, vms: Sequence[VirtualMachine], observed_at: datetime
    ) -> UpsertResult: ...


class InventoryReadRepository(Protocol):
    """조회 경로. 범위 없는 전체 조회 함수를 만들지 않는다 (ROADMAP §4.3).

    `scope`가 첫 필수 인자다. 선택 인자로 두면 빠뜨리기 쉽다.
    """

    async def search_vms(
        self, scope: AccessScope, connection_id: UUID | None, page: Page
    ) -> PagedResult[VmSummary]: ...

    async def count_active_vms(self, connection_id: UUID) -> int: ...
