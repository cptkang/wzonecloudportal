"""커넥터 Protocol과 연결 검증·수집 결과 타입 (계획 03).

이 모듈은 `scripts/arch_check.py`의 **읽기 전용 검사 대상**이다.
자원 변경 접두사(`create_`, `power_`, `delete_` …)를 쓰면 error로 차단된다.

저장소 Protocol은 `src/domain/repositories.py`에 둔다 — 저장소는 포탈 자체 데이터를
쓰므로 `create_*` 같은 이름이 정당하고, 이 파일의 검사 대상과 성격이 다르다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from src.domain.enums import CheckStage, ResourceType
from src.domain.resource import VirtualMachine

if TYPE_CHECKING:  # 순환 참조 방지 — 팩토리 타입 힌트에만 쓴다
    from src.domain.connection import Connection


# ── 연결 검증 결과 (FR-106) ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: CheckStage
    passed: bool
    #: 이전 단계 실패로 확인하지 못함
    skipped: bool = False
    #: 자격증명을 포함하지 않는다 (NFR-203)
    detail: str | None = None
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectionCheckResult:
    """단계별 결과.

    "접속 불가"·"인증 실패"·"권한 부족"은 관리자의 조치가 완전히 다르다.
    단일 boolean으로 반환하면 관리자가 원인을 추측해야 한다.
    """

    stages: tuple[StageResult, ...]
    readable_types: frozenset[ResourceType] = frozenset()
    server_version: str | None = None

    @property
    def is_usable(self) -> bool:
        return all(s.passed for s in self.stages) and not any(s.skipped for s in self.stages)

    @property
    def failed_stage(self) -> CheckStage | None:
        for s in self.stages:
            if not s.passed and not s.skipped:
                return s.stage
        return None


# ── 수집 결과 메타 (FR-204·205) ──────────────────────────────


@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    resource_type: ResourceType
    collected_count: int
    failed: bool
    #: capability 미지원으로 수집하지 않음 (Step 3에서 사용)
    skipped: bool = False
    error: str | None = None
    elapsed_ms: int | None = None


# ── 커넥터 Protocol ──────────────────────────────────────────


@runtime_checkable
class HypervisorInventoryReader(Protocol):
    """하이퍼바이저 인벤토리 조회 인터페이스.

    구현체는 조회만 수행하며 자원을 변경하는 API를 호출하지 않는다 (CST-01, D-005).

    Step 1 범위: VM 목록과 연결 검증. 나머지 `list_*`는 Step 4~6에서 추가한다.
    빈 구현으로 채운 메서드를 미리 두지 않는다 — 계약 테스트가 껍데기가 되고
    미지원과 미구현이 섞인다.

    사용 패턴::

        async with reader:
            async for vm in reader.list_virtual_machines():
                ...
    """

    @property
    def connection_id(self) -> UUID: ...

    async def start_session(self) -> None:
        """하이퍼바이저 세션을 연다. 자격증명은 이 시점에만 복호화한다."""

    async def close_session(self) -> None:
        """세션을 닫는다. 미호출 시 하이퍼바이저에 세션이 누적된다."""

    async def __aenter__(self) -> "HypervisorInventoryReader": ...

    async def __aexit__(self, *exc_info: object) -> None: ...

    async def check_connection(self) -> ConnectionCheckResult:
        """단계별 연결 상태를 검증한다 (FR-106). 예외 대신 결과로 반환한다."""

    def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        """VM을 페이지 단위로 흘려보낸다.

        `async def`로 선언하지 않는 이유는 계획 03 §2.1 참조 —
        async generator 함수는 호출 즉시 generator를 반환하므로
        `def ... -> AsyncIterator[T]`가 올바른 선언이다.
        """

    def get_outcomes(self) -> Sequence[CollectionOutcome]:
        """직전 수집의 자원 유형별 결과. 부분 실패 판정에 쓴다 (FR-204)."""


#: 구체 어댑터 선택은 interface/entry 계층에서만 한다 (계획 03 §7).
#: application/orchestration이 어댑터를 import하면 arch-check 특화 규칙 위반이다.
ReaderFactory = Callable[["Connection"], HypervisorInventoryReader]
