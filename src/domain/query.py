"""조회 모델 — 페이징과 목록 DTO (계획 07 §2).

계획 07은 이 타입들을 `src/application/dto.py`에 두라고 했으나, **저장소(infrastructure)가
이들을 반환**하므로 application에 두면 infrastructure→application 역방향 의존이 된다
(D-002 계층 규칙 위반). 도메인 읽기 모델로 domain에 둔다 (D-016).

목록과 상세를 분리한다 — 목록에 전체 속성을 실으면 5,000건 조회 시 응답이 수십 MB가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Generic, TypeVar
from uuid import UUID

from src.domain.enums import (
    GuestInfoAvailability,
    HypervisorKind,
    OsSource,
    PowerState,
    ResourceLifecycle,
)
from src.domain.exceptions import ValidationError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page:
    """오프셋 페이징. `page`/`size`가 아니라 `offset`/`limit`이다 (ROADMAP §9.2)."""

    offset: int = 0
    limit: int = 50
    sort_by: str = "name"
    sort_desc: bool = False

    MAX_LIMIT: ClassVar[int] = 500
    #: **화이트리스트가 필수다.** 정렬 컬럼을 문자열로 받아 SQL에 넣으면 인젝션 경로가 된다.
    #: Step 4 이후 컬럼이 늘어도 이 집합에 추가하는 방식은 그대로다.
    ALLOWED_SORT: ClassVar[frozenset[str]] = frozenset(
        {"name", "power_state", "guest_os_name", "vcpu_count", "memory_mb", "last_seen_at"}
    )

    def validate(self) -> None:
        if self.offset < 0:
            raise ValidationError("offset은 0 이상이어야 합니다.", field="offset")
        if not (1 <= self.limit <= self.MAX_LIMIT):
            raise ValidationError(f"limit은 1~{self.MAX_LIMIT} 범위여야 합니다.", field="limit")
        if self.sort_by not in self.ALLOWED_SORT:
            raise ValidationError(
                f"정렬할 수 없는 컬럼입니다: {self.sort_by}", field="sort_by"
            )


@dataclass(frozen=True, slots=True)
class PagedResult(Generic[T]):
    items: tuple[T, ...]
    total: int
    page: Page
    #: 대량에서 COUNT(*)를 근사치로 전환할 때 True (Step 4)
    total_is_estimate: bool = False


@dataclass(frozen=True, slots=True)
class VmSummary:
    """목록용 (FR-401).

    Step 1은 §5.3의 7개 컬럼만 채운다. IP·디스크·스냅샷은 Step 4에서 추가된다.
    """

    resource_id: UUID
    name: str
    native_id: str
    connection_id: UUID
    connection_name: str
    hypervisor: HypervisorKind
    power_state: PowerState
    vcpu_count: int | None
    memory_mb: int | None
    configured_os: str | None
    guest_availability: GuestInfoAvailability
    guest_os_name: str | None
    guest_os_source: OsSource | None
    guest_hostname: str | None
    guest_observed_at: datetime | None
    host_native_id: str | None
    lifecycle: ResourceLifecycle
    last_seen_at: datetime
