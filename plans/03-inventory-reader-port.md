# 03. 커넥터 Protocol (HypervisorInventoryReader)

> Wave: 1
> 계층: domain (`src/domain/ports.py`)
> 담당 요건: FR-301, FR-106(연결 테스트), FR-501, NFR-202(쓰기 금지), NFR-106(확장성)
> 의존: 02
> 관련 결정: D-003, D-005

## 1. 목적

vCenter/Hyper-V 어댑터가 구현할 **단일 인터페이스**를 정의한다.
유스케이스와 수집 워커는 이 Protocol만 알고 구체 어댑터는 모른다 (D-003).

이 파일은 `arch_check.py`의 **읽기 전용 검사 대상**이다. 자원 변경 메서드를 정의하면 error로 차단된다.

## 2. Protocol 정의

```python
class HypervisorInventoryReader(Protocol):
    """하이퍼바이저 인벤토리 조회 인터페이스.

    구현체는 조회만 수행하며, 하이퍼바이저 상태를 변경하는 어떤 API도 호출하지 않는다.
    """

    @property
    def capabilities(self) -> ReaderCapabilities: ...

    async def start_session(self) -> None: ...
    async def close_session(self) -> None: ...

    async def check_connection(self) -> ConnectionCheckResult: ...

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]: ...
    async def list_hosts(self) -> AsyncIterator[Host]: ...
    async def list_clusters(self) -> AsyncIterator[Cluster]: ...
    async def list_datastores(self) -> AsyncIterator[Datastore]: ...
    async def list_networks(self) -> AsyncIterator[Network]: ...
    async def list_snapshots(self) -> AsyncIterator[Snapshot]: ...
```

**`start_session` / `close_session`은 `arch_check.py`의 허용 목록에 등록되어 있다.**
하이퍼바이저 자원이 아니라 커넥션 세션을 다루므로 읽기 전용 원칙에 위배되지 않는다.

### 2.1 왜 `AsyncIterator`인가

수천~수만 건을 리스트로 한 번에 반환하면 메모리에 전부 적재된다.
vCenter는 `RetrievePropertiesEx`가 토큰 기반 페이징을 제공하고(`docs/00_research_notes.md` §4.2),
Hyper-V도 호스트별로 나눠 조회하므로, **어댑터가 페이지 단위로 yield**하고 저장소가 배치로 커밋하게 한다.

호출 측 패턴:

```python
async for vm in reader.list_virtual_machines():
    batch.append(vm)
    if len(batch) >= BATCH_SIZE:
        await repository.upsert_virtual_machines(batch)
        batch.clear()
```

## 3. Capability — 하이퍼바이저별 지원 범위 (핵심 설계)

vCenter에는 있고 Hyper-V에는 없는 개념(ResourcePool, Datacenter/Folder, vSphere Tags)이 있다.
**"미지원"과 "수집 실패"와 "값 없음"은 서로 다르며**, 이를 구분하지 못하면 UI가 잘못된 정보를 준다.

```python
@dataclass(frozen=True)
class ReaderCapabilities:
    kind: HypervisorKind
    supports_resource_pool: bool
    supports_folder_hierarchy: bool
    supports_native_tags: bool
    supports_cluster: bool              # Hyper-V 단독 호스트 연결이면 False
    supports_incremental: bool          # 증분 갱신 가능 여부 (FR-207)
    collectable_types: frozenset[ResourceType]
```

| 판정 | 의미 | UI 표시 |
|---|---|---|
| capability = False | 하이퍼바이저가 개념 자체를 지원하지 않음 | "해당 없음" (회색) |
| `GuestInfo.is_collected` = False | 도구 미동작으로 수집 불가 | "수집 불가 — 사유" (주의) |
| 값이 `None`이고 위 둘 다 아님 | 실제로 비어 있음 | 빈칸 |

**미지원 메서드 호출 시**: 예외를 던지지 않고 **빈 이터레이터를 반환**한다.
호출 측이 매번 capability를 확인해야 한다면 분기가 유스케이스로 새어 나오기 때문이다.
capability는 **UI 표시와 수집 계획 수립**에만 쓴다.

## 4. 연결 테스트 결과 (FR-106)

연결 테스트는 "성공/실패" 2값이 아니라 **단계별 결과**를 반환해야 한다.
접속은 되는데 권한이 없는 경우와, 아예 닿지 않는 경우는 관리자의 조치가 다르다.

```python
class CheckStage(StrEnum):
    REACHABLE = "reachable"          # 네트워크 도달
    TLS_VALID = "tls_valid"          # 인증서 검증 (FR-115)
    AUTHENTICATED = "authenticated"  # 인증 성공
    AUTHORIZED = "authorized"        # 필요한 자원 유형 조회 권한

@dataclass(frozen=True)
class StageResult:
    stage: CheckStage
    passed: bool
    detail: str | None               # 실패 사유 (자격증명 미포함 — NFR-203)

@dataclass(frozen=True)
class ConnectionCheckResult:
    stages: tuple[StageResult, ...]
    readable_types: frozenset[ResourceType]   # 실제 조회 가능한 자원 유형
    server_version: str | None

    @property
    def is_usable(self) -> bool:
        return all(s.passed for s in self.stages)
```

**권한 확인 방법**: 각 자원 유형에 대해 **1건만 조회**해 본다. 전량 조회는 연결 테스트에 부적절하다.

## 5. 수집 결과 메타 (FR-204, FR-205)

어댑터는 자원만 반환하고 수집 통계는 워커가 집계한다.
다만 **부분 실패**(예: VM은 조회됐으나 Datastore 권한 없음)를 워커가 알아야 하므로, 어댑터는 유형별 실패를 예외가 아닌 결과로 전달한다.

```python
@dataclass
class CollectionOutcome:
    resource_type: ResourceType
    collected_count: int
    failed: bool
    error: str | None
```

어댑터는 `list_*` 이터레이터가 끝난 뒤 `get_last_outcomes()`로 이를 노출한다.
**한 자원 유형의 실패가 다른 유형의 수집을 중단시키면 안 된다** (FR-204).

## 6. 팩토리 (계층 경계)

구체 어댑터 선택은 **`interface`/`entry` 계층**에서 한다. 유스케이스·워커가 어댑터를 import하면 arch-check 위반이다.

```python
# src/api/deps.py 또는 src/main.py — interface/entry 계층
def create_reader(connection: Connection) -> HypervisorInventoryReader:
    match connection.kind:
        case ConnectionKind.VCENTER:
            from src.infrastructure.vcenter import VCenterInventoryReader
            return VCenterInventoryReader(connection)
        case ConnectionKind.HYPERV_HOST | ConnectionKind.HYPERV_CLUSTER:
            from src.infrastructure.hyperv import HyperVInventoryReader
            return HyperVInventoryReader(connection)
        case ConnectionKind.SCVMM:
            raise NotImplementedError("SCVMM 연동 미구현 — CST-09 확정 후")
```

워커에는 이 팩토리를 **함수로 주입**한다:

```python
# src/orchestration/collector.py — orchestration 계층
ReaderFactory = Callable[[Connection], HypervisorInventoryReader]

class InventoryCollector:
    def __init__(self, reader_factory: ReaderFactory, repo: InventoryRepository) -> None: ...
```

## 7. 저장소 Protocol

같은 파일에 저장소 인터페이스도 정의한다 (구현은 계획 06).

```python
class InventoryRepository(Protocol):
    async def find_by_identity(self, keys: list[IdentityKey]) -> UUID | None: ...
    async def upsert_virtual_machines(self, vms: Sequence[VirtualMachine]) -> UpsertResult: ...
    async def mark_missing(self, connection_id: UUID, seen_ids: set[str], at: datetime) -> int: ...
    ...
```

## 8. 구현 순서

1. `ReaderCapabilities`, `ConnectionCheckResult` 등 값 객체 → 검증: 불변성
2. `HypervisorInventoryReader` Protocol → 검증: **`arch_check.py` 읽기 전용 검사 통과**
3. `InventoryRepository` Protocol
4. `tests/fakes/fake_reader.py` 목 구현 → 검증: Protocol 만족(`isinstance` 체크 또는 mypy)
5. 계약 테스트 스위트 뼈대 (`tests/contract/`) → 04·05가 이 스위트를 그대로 통과해야 함

## 9. 완료 기준

- [ ] `python scripts/arch_check.py --ci` 통과 — **읽기 전용 위반 0건**
- [ ] Protocol의 모든 메서드가 조회 동사로 명명됨
- [ ] 목 커넥터가 Protocol을 만족하고 계약 테스트를 통과
- [ ] `ConnectionCheckResult`가 4단계를 개별 보고
- [ ] mypy strict 통과

## 10. 주의사항

- **`connect`/`disconnect` 대신 `start_session`/`close_session`을 쓴다.** 전자는 arch-check 허용 목록에 없다.
- Protocol에 `refresh_`, `sync_` 같은 동사를 쓰고 싶어질 수 있으나, 검사 접두사에 걸리지 않더라도 **의미가 "쓰기"로 읽히면 피한다.** 수집은 `collect_`/`list_`다.
- 미지원 기능에 예외를 던지지 않는다 (§3). 유스케이스에 하이퍼바이저 분기가 생기는 원인이 된다.
- SCVMM 연동은 CST-09 확정 전까지 `NotImplementedError`로 두되, `ConnectionKind`에는 미리 값을 포함해 스키마 변경을 피한다.
