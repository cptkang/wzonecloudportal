# 03. 커넥터 Protocol (HypervisorInventoryReader)

> Wave: 1 · 계층: domain (`src/domain/ports.py`)
> 담당 요건: FR-301, FR-106, FR-204, FR-501, NFR-202, NFR-106
> 의존: 02 · 관련 결정: D-003, D-005

## 1. 목적

vCenter/Hyper-V 어댑터가 구현할 단일 인터페이스와, 두 어댑터가 함께 통과해야 하는 **계약 테스트 스위트**를 정의한다.

`src/domain/ports.py`는 `arch_check.py`의 **읽기 전용 검사 대상**이다. 자원 변경 접두사를 쓰면 error로 차단된다.

---

## 2. 전체 Protocol 정의

```python
# src/domain/ports.py
from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence, runtime_checkable


@runtime_checkable
class HypervisorInventoryReader(Protocol):
    """하이퍼바이저 인벤토리 조회 인터페이스.

    구현체는 조회만 수행하며 하이퍼바이저 상태를 변경하는 API를 호출하지 않는다
    (spec.md CST-01, NFR-202).

    사용 패턴:
        async with reader:
            async for vm in reader.list_virtual_machines():
                ...
    """

    # ── 메타 ──────────────────────────────────────
    @property
    def capabilities(self) -> ReaderCapabilities:
        """이 어댑터가 지원하는 기능 범위."""

    @property
    def connection_id(self) -> UUID: ...

    # ── 세션 (arch_check 허용 목록에 등록됨) ───────
    async def start_session(self) -> None:
        """하이퍼바이저 세션을 연다. 자격증명은 이 시점에만 복호화한다."""

    async def close_session(self) -> None:
        """세션을 닫는다. 미호출 시 하이퍼바이저에 세션이 누적된다."""

    async def __aenter__(self) -> "HypervisorInventoryReader": ...
    async def __aexit__(self, *exc_info: object) -> None: ...

    # ── 연결 검증 ─────────────────────────────────
    async def check_connection(self) -> ConnectionCheckResult:
        """단계별 연결 상태를 검증한다 (FR-106). 예외를 던지지 않고 결과로 반환한다."""

    # ── 인벤토리 조회 ─────────────────────────────
    def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]: ...
    def list_hosts(self) -> AsyncIterator[Host]: ...
    def list_clusters(self) -> AsyncIterator[Cluster]: ...
    def list_datastores(self) -> AsyncIterator[Datastore]: ...
    def list_networks(self) -> AsyncIterator[Network]: ...
    def list_snapshots(self) -> AsyncIterator[Snapshot]: ...

    # ── 수집 결과 메타 ────────────────────────────
    def get_outcomes(self) -> Sequence[CollectionOutcome]:
        """직전 수집의 자원 유형별 결과. 부분 실패 판정에 사용한다 (FR-204)."""
```

### 2.1 `list_*`가 `async def`가 아닌 이유

`async def` + `yield`인 async generator 함수는 **호출 시점에 코루틴이 아니라 async generator를 반환**한다.
Protocol에서는 `def ... -> AsyncIterator[T]`로 선언해야 `await` 없이 `async for`에 바로 쓸 수 있다.

```python
# 올바름 — 호출 즉시 async generator
async for vm in reader.list_virtual_machines(): ...

# Protocol을 async def로 선언하면 아래처럼 써야 해서 어색하다
async for vm in await reader.list_virtual_machines(): ...
```

구현체는 `async def list_virtual_machines(self) -> AsyncIterator[VM]: ... yield ...`로 작성하면 된다.

### 2.2 AsyncIterator를 쓰는 이유

수천~수만 건을 리스트로 반환하면 메모리에 전부 적재된다.
vCenter는 `RetrievePropertiesEx`가 토큰 페이징을 제공하고(`docs/00_research_notes.md` §4.2), Hyper-V는 호스트별로 나눠 조회하므로
어댑터가 페이지 단위로 yield하고 저장소가 배치 커밋한다.

```python
BATCH_SIZE = 500
batch: list[VirtualMachine] = []
async for vm in reader.list_virtual_machines():
    batch.append(vm)
    if len(batch) >= BATCH_SIZE:
        await repo.upsert_virtual_machines(batch)
        batch.clear()
if batch:
    await repo.upsert_virtual_machines(batch)
```

---

## 3. ReaderCapabilities

```python
@dataclass(frozen=True, slots=True)
class ReaderCapabilities:
    """하이퍼바이저별 지원 범위.

    '미지원'과 '수집 실패'와 '값 없음'은 서로 다르다. 이를 구분하지 못하면
    UI가 Hyper-V VM의 리소스풀을 '수집 실패'로 잘못 표시한다.
    """
    kind: HypervisorKind
    connection_kind: ConnectionKind
    supports_resource_pool: bool
    supports_folder_hierarchy: bool
    supports_native_tags: bool           # 하이퍼바이저 네이티브 태그(vSphere Tags). 양쪽 모두 False (D-010)
    supports_cluster: bool
    supports_incremental: bool
    collectable_types: frozenset[ResourceType]

    def supports(self, rtype: ResourceType) -> bool:
        return rtype in self.collectable_types


VCENTER_CAPABILITIES = ReaderCapabilities(
    kind=HypervisorKind.VCENTER,
    connection_kind=ConnectionKind.VCENTER,
    supports_resource_pool=True,
    supports_folder_hierarchy=True,
    supports_native_tags=False,      # vSphere Tags는 REST 전용 — 미지원 확정 (D-010)
    supports_cluster=True,
    supports_incremental=False,      # Phase 1은 전량 수집
    collectable_types=frozenset(ResourceType),
)


def hyperv_capabilities(connection_kind: ConnectionKind) -> ReaderCapabilities:
    return ReaderCapabilities(
        kind=HypervisorKind.HYPERV,
        connection_kind=connection_kind,
        supports_resource_pool=False,
        supports_folder_hierarchy=False,
        supports_native_tags=False,
        supports_cluster=(connection_kind is ConnectionKind.HYPERV_CLUSTER),
        supports_incremental=False,
        collectable_types=frozenset(ResourceType) - {ResourceType.NETWORK}
            if connection_kind is ConnectionKind.HYPERV_HOST else frozenset(ResourceType),
    )
```

### 3.1 판정 표

| 판정 | 조건 | UI 표시 (계획 11) |
|---|---|---|
| 미지원 | `capabilities.supports_*` = False | `해당 없음` (회색) |
| 수집 불가 | `GuestInfo.is_collected` = False | `수집 불가 — 사유` (주의색) |
| 값 없음 | 위 둘 다 아니고 `None` | `—` (회색) |

### 3.2 미지원 메서드 호출 시 규약

**예외를 던지지 않고 빈 이터레이터를 반환한다.**

```python
async def list_clusters(self) -> AsyncIterator[Cluster]:
    if not self.capabilities.supports_cluster:
        return                       # 빈 async generator
        yield                        # 도달 불가 — generator로 만들기 위한 구문
```

호출 측이 매번 capability를 확인해야 한다면 하이퍼바이저 분기가 유스케이스로 새어 나온다.
capability는 **UI 표시와 수집 계획 수립**에만 쓴다.

---

## 4. 연결 테스트 결과 (FR-106)

```python
class CheckStage(StrEnum):
    REACHABLE = "reachable"
    TLS_VALID = "tls_valid"
    AUTHENTICATED = "authenticated"
    AUTHORIZED = "authorized"


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: CheckStage
    passed: bool
    skipped: bool = False           # 이전 단계 실패로 미확인
    detail: str | None = None       # 자격증명 미포함 (NFR-203)
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectionCheckResult:
    stages: tuple[StageResult, ...]
    readable_types: frozenset[ResourceType] = frozenset()
    server_version: str | None = None
    server_build: str | None = None

    @property
    def is_usable(self) -> bool:
        return all(s.passed for s in self.stages if not s.skipped) and not any(
            s.skipped for s in self.stages
        )

    @property
    def failed_stage(self) -> CheckStage | None:
        for s in self.stages:
            if not s.passed and not s.skipped:
                return s.stage
        return None
```

**단계를 나누는 이유**: "접속 불가"·"인증 실패"·"권한 부족"은 관리자의 조치가 완전히 다르다.
단일 boolean으로 반환하면 관리자가 원인을 추측해야 한다.

### 4.1 공통 구현 헬퍼

두 어댑터가 같은 구조로 구현하도록 골격을 제공한다. **어댑터끼리 참조하면 안 되므로** 이 헬퍼는 `src/utils/`에 둔다.

```python
# src/utils/check.py
class StageRunner:
    """연결 테스트 단계를 순차 실행하고, 실패 시 이후 단계를 skipped로 기록한다."""

    def __init__(self) -> None:
        self._results: list[StageResult] = []
        self._aborted = False

    async def run(self, stage: CheckStage, fn: Callable[[], Awaitable[str | None]]) -> None:
        if self._aborted:
            self._results.append(StageResult(stage=stage, passed=False, skipped=True))
            return
        started = time.monotonic()
        try:
            detail = await fn()
            self._results.append(StageResult(
                stage=stage, passed=True, detail=detail,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
        except Exception as exc:
            self._results.append(StageResult(
                stage=stage, passed=False, detail=sanitize_error(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
            self._aborted = True

    @property
    def results(self) -> tuple[StageResult, ...]:
        return tuple(self._results)
```

`sanitize_error`는 자격증명 패턴을 제거한다 (계획 10 §2.5의 마스킹 로직 재사용).

---

## 5. 수집 결과 메타 (FR-204·205)

```python
@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    resource_type: ResourceType
    collected_count: int
    failed: bool
    skipped: bool = False           # capability 미지원으로 수집 안 함
    error: str | None = None
    elapsed_ms: int | None = None
```

**한 자원 유형의 실패가 다른 유형의 수집을 중단시키면 안 된다** (FR-204).
어댑터는 `list_*` 안에서 예외를 잡아 outcome에 기록하고 이터레이션을 정상 종료한다.

```python
async def list_datastores(self) -> AsyncIterator[Datastore]:
    started = time.monotonic()
    count = 0
    try:
        async for ds in self._collect_datastores():
            count += 1
            yield ds
    except PortalError as exc:
        self._outcomes.append(CollectionOutcome(
            resource_type=ResourceType.DATASTORE, collected_count=count,
            failed=True, error=str(exc),
        ))
        return                      # 예외를 전파하지 않고 종료
    self._outcomes.append(CollectionOutcome(
        resource_type=ResourceType.DATASTORE, collected_count=count, failed=False,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    ))
```

> **예외**: `AuthenticationError`는 전파한다. 세션 자체가 무효이므로 다른 유형도 모두 실패한다.
> 수집 워커가 이를 받아 연결을 자격증명 오류로 전환한다 (계획 06 §7.2).

---

## 6. 저장소 Protocol

```python
@dataclass(frozen=True, slots=True)
class UpsertResult:
    created: int
    updated: int
    unchanged: int
    changes: tuple[ResourceChange, ...] = ()          # 계획 12
    duplicate_candidates: tuple[DuplicateCandidate, ...] = ()   # FR-308


class InventoryRepository(Protocol):
    """인벤토리 영속화. 구현은 계획 06."""

    async def upsert_virtual_machines(
        self, connection_id: UUID, vms: Sequence[VirtualMachine], observed_at: datetime
    ) -> UpsertResult: ...

    async def upsert_hosts(self, connection_id: UUID, hosts: Sequence[Host], observed_at: datetime) -> UpsertResult: ...
    # clusters / datastores / networks / snapshots 동일 시그니처

    async def mark_missing(
        self, connection_id: UUID, resource_type: ResourceType,
        seen_native_ids: set[str], at: datetime,
    ) -> MissingResult:
        """이번 수집에서 보이지 않은 자원을 missing으로 전환한다.

        수집에 실패한 자원 유형에는 절대 호출하면 안 된다 (계획 06 §11).
        """

    async def count_active(self, connection_id: UUID, resource_type: ResourceType) -> int: ...

    async def disconnect_resources(self, connection_id: UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class MissingResult:
    marked: int
    existing_total: int

    @property
    def missing_ratio(self) -> float:
        return self.marked / self.existing_total if self.existing_total else 0.0
```

`missing_ratio`는 연결 대상 변경 감지(FR-111)에 사용한다.

---

## 7. 팩토리 (계층 경계)

구체 어댑터 선택은 `interface`/`entry` 계층에서만 한다.
`application`/`orchestration`이 어댑터를 import하면 **arch-check 특화 규칙 6번 위반**이다.

```python
# src/domain/ports.py — 타입만 정의
ReaderFactory = Callable[[Connection], HypervisorInventoryReader]
```

```python
# src/api/deps.py 또는 src/main.py — interface/entry 계층
def create_reader(conn: Connection) -> HypervisorInventoryReader:
    match conn.kind:
        case ConnectionKind.VCENTER:
            from src.infrastructure.vcenter import VCenterInventoryReader
            return VCenterInventoryReader(conn)
        case ConnectionKind.HYPERV_HOST | ConnectionKind.HYPERV_CLUSTER:
            from src.infrastructure.hyperv import HyperVHostInventoryReader
            return HyperVHostInventoryReader(conn)          # 경로 A (계획 05 §2)
        case ConnectionKind.SCVMM:
            from src.infrastructure.hyperv import ScvmmInventoryReader
            return ScvmmInventoryReader(conn)               # 경로 B (계획 05 §2)
```

```python
# src/orchestration/collector.py — 팩토리를 주입받는다
class InventoryCollector:
    def __init__(self, reader_factory: ReaderFactory, repo: InventoryRepository) -> None:
        self._reader_factory = reader_factory
        self._repo = repo
```

---

## 8. 목 커넥터 (`tests/fakes/fake_reader.py`)

실제 하이퍼바이저에 연결하지 않으므로(CST-04) 목 구현이 개발·테스트의 기반이다.

```python
class FakeInventoryReader:
    """테스트용 목 커넥터. 시나리오를 주입하여 다양한 상황을 재현한다."""

    def __init__(
        self,
        connection: Connection,
        *,
        vms: Sequence[VirtualMachine] = (),
        hosts: Sequence[Host] = (),
        fail_types: set[ResourceType] | None = None,     # 부분 실패 재현
        auth_error: bool = False,                        # 인증 실패 재현
        unreachable: bool = False,
        capabilities: ReaderCapabilities | None = None,
    ) -> None: ...

    async def start_session(self) -> None:
        if self._auth_error:
            raise AuthenticationError("인증에 실패했습니다.")
        if self._unreachable:
            raise UnreachableError("서버에 연결할 수 없습니다.")

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        if ResourceType.VIRTUAL_MACHINE in self._fail_types:
            self._outcomes.append(CollectionOutcome(
                resource_type=ResourceType.VIRTUAL_MACHINE, collected_count=0,
                failed=True, error="시뮬레이션 실패",
            ))
            return
        for vm in self._vms:
            yield vm
```

### 8.1 픽스처 빌더

```python
def make_vm(
    *, name: str = "test-vm", connection_id: UUID | None = None,
    native_id: str | None = None, bios_uuid: str | None = None,
    guest_available: bool = True, ipv4: Sequence[str] = ("10.0.0.10",),
    macs: Sequence[str] = ("00:50:56:aa:bb:cc",), power: PowerState = PowerState.ON,
    vcpu: int = 2, memory_mb: int = 4096,
) -> VirtualMachine:
    """테스트용 VM을 만든다. 필요한 필드만 지정하고 나머지는 기본값."""
```

---

## 9. 계약 테스트 스위트 (`tests/contract/`)

**두 어댑터가 같은 스위트를 통과해야 한다.** 이것이 Protocol 계약의 실질적 보장이다.

```python
# tests/contract/test_reader_contract.py

@pytest.fixture(params=["vcenter", "hyperv"])
def reader_factory(request, vcenter_mock_server, hyperv_mock_server):
    """어댑터별 리더를 생성한다. 실제 하이퍼바이저 대신 목 서버를 사용한다."""
    ...


class TestReaderContract:
    """모든 HypervisorInventoryReader 구현이 만족해야 하는 계약."""

    async def test_protocol_conformance(self, reader):
        assert isinstance(reader, HypervisorInventoryReader)

    async def test_session_lifecycle(self, reader):
        await reader.start_session()
        await reader.close_session()

    async def test_context_manager_closes_on_error(self, reader):
        with pytest.raises(RuntimeError):
            async with reader:
                raise RuntimeError("boom")
        assert reader.is_session_closed          # 실패 경로에서도 세션 해제

    async def test_list_vms_returns_domain_models(self, reader):
        async with reader:
            vms = [vm async for vm in reader.list_virtual_machines()]
        assert all(isinstance(vm, VirtualMachine) for vm in vms)
        # 하이퍼바이저 고유 객체가 새어 나오지 않아야 한다
        assert all(type(vm).__module__.startswith("src.domain") for vm in vms)

    async def test_required_fields_present(self, reader):
        """spec.md §2.2 필수(✔) 속성이 채워지는지."""
        async with reader:
            vm = await anext(reader.list_virtual_machines())
        assert vm.native_id and vm.name
        assert vm.power_state is not PowerState.UNKNOWN
        assert vm.cpu.total_vcpu > 0
        assert vm.memory.assigned_mb > 0
        assert vm.guest is not None                      # 상태는 무엇이든 존재해야

    async def test_mac_normalized(self, reader):
        async with reader:
            vm = await anext(reader.list_virtual_machines())
        for mac in vm.mac_addresses:
            assert mac == mac.lower()
            assert re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac)

    async def test_link_local_ip_filtered(self, reader_with_link_local):
        """게스트 도구가 보고하는 링크로컬 주소가 걸러져야 한다."""
        async with reader_with_link_local as reader:
            vm = await anext(reader.list_virtual_machines())
        assert not any(ip.startswith("169.254.") for ip in vm.guest.ipv4_addresses)
        assert not any(ip.startswith("fe80:") for ip in vm.guest.ipv6_addresses)

    async def test_tools_not_installed_maps_correctly(self, reader_without_tools):
        async with reader_without_tools as reader:
            vm = await anext(reader.list_virtual_machines())
        assert vm.guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED
        assert not vm.guest.is_collected
        assert vm.guest.ipv4_addresses == ()

    async def test_unsupported_type_returns_empty(self, reader):
        """미지원 자원 유형은 예외가 아니라 빈 결과."""
        async with reader:
            if not reader.capabilities.supports_cluster:
                clusters = [c async for c in reader.list_clusters()]
                assert clusters == []

    async def test_auth_failure_raises_non_retryable(self, reader_with_bad_credentials):
        with pytest.raises(AuthenticationError) as ei:
            await reader_with_bad_credentials.start_session()
        assert ei.value.retryable is False

    async def test_partial_failure_records_outcome(self, reader_failing_datastores):
        """한 유형 실패가 다른 유형 수집을 막지 않는다."""
        async with reader_failing_datastores as reader:
            vms = [v async for v in reader.list_virtual_machines()]
            dss = [d async for d in reader.list_datastores()]
        assert len(vms) > 0
        assert dss == []
        outcomes = {o.resource_type: o for o in reader.get_outcomes()}
        assert outcomes[ResourceType.DATASTORE].failed is True
        assert outcomes[ResourceType.VIRTUAL_MACHINE].failed is False

    async def test_check_connection_reports_stages(self, reader):
        result = await reader.check_connection()
        assert {s.stage for s in result.stages} == set(CheckStage)

    async def test_check_connection_no_credentials_in_detail(self, reader_with_bad_credentials):
        result = await reader_with_bad_credentials.check_connection()
        blob = " ".join(s.detail or "" for s in result.stages)
        assert reader_with_bad_credentials.connection.password.get_secret_value() not in blob

    async def test_no_write_methods(self, reader):
        """읽기 전용 원칙 — public 메서드에 변경 동사가 없어야 한다."""
        forbidden = ("create_", "delete_", "power_", "modify_", "migrate_", "remove_")
        allowed = {"start_session", "stop_session", "close_session", "reset_session"}
        for attr in dir(reader):
            if attr.startswith("_") or attr in allowed:
                continue
            assert not attr.startswith(forbidden), f"쓰기 메서드 의심: {attr}"
```

### 9.1 목 서버 전략

실제 하이퍼바이저 대신 응답을 재현한다.

| 어댑터 | 방법 |
|---|---|
| vCenter | pyVmomi 객체를 흉내낸 스텁 + `PropertyCollector` 응답 fixture (JSON으로 캡처한 속성 맵) |
| Hyper-V | PowerShell/WMI 실행 함수를 monkeypatch하고 **실제 `ConvertTo-Json` 출력 샘플**을 반환 |

**실제 출력 샘플을 fixture로 보관하는 것이 중요하다.** 손으로 만든 가짜 응답은 실제 형식과 달라 통과해도 의미가 없다.
샘플은 검증 환경에서 한 번 캡처해 `tests/fixtures/`에 커밋한다 (자격증명·실명 마스킹 후).

---

## 10. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `ReaderCapabilities`, `CheckStage`, `StageResult`, `ConnectionCheckResult` | 불변성, `is_usable` 판정 |
| 2 | `CollectionOutcome`, `UpsertResult`, `MissingResult` | `missing_ratio` 계산 |
| 3 | `HypervisorInventoryReader` Protocol | **`arch_check.py` 읽기 전용 검사 통과** |
| 4 | `InventoryRepository` Protocol | mypy로 시그니처 확인 |
| 5 | `src/utils/check.py` `StageRunner` | 실패 후 단계가 skipped로 기록 |
| 6 | `tests/fakes/fake_reader.py` + 픽스처 빌더 | Protocol 만족(`isinstance`) |
| 7 | 계약 테스트 스위트 | 목 커넥터로 전체 통과 |

## 11. 완료 기준

- [ ] `python scripts/arch_check.py --ci` 통과 — 읽기 전용 위반 0건
- [ ] Protocol의 모든 조회 메서드가 `list_`/`get_`/`check_`로 명명됨
- [ ] `FakeInventoryReader`가 `isinstance(x, HypervisorInventoryReader)` 만족
- [ ] 계약 테스트 14종이 목 커넥터로 전부 통과
- [ ] `ConnectionCheckResult`가 4단계를 개별 보고하고 skipped를 구분
- [ ] 미지원 자원 유형 호출 시 예외 없이 빈 결과
- [ ] 부분 실패 시 다른 유형 수집이 계속되고 outcome에 기록됨
- [ ] mypy strict 통과

## 12. 주의사항

- **`connect`/`disconnect` 대신 `start_session`/`close_session`을 쓴다.** 전자는 arch-check 허용 목록에 없다.
- `refresh_`, `sync_`처럼 쓰기로 읽히는 동사를 피한다. 검사에 걸리지 않아도 의미가 오해된다.
- 미지원 기능에 예외를 던지면 유스케이스에 하이퍼바이저 분기가 생긴다 (§3.2).
- `runtime_checkable` Protocol의 `isinstance`는 **메서드 존재만 확인**하고 시그니처는 보지 않는다. 타입 검증은 mypy에 의존한다.
- 계약 테스트를 어댑터별로 나눠 쓰면 계약이 갈라진다. **하나의 스위트를 파라미터화**한다.
