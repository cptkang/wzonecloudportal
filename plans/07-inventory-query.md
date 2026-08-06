# 07. 인벤토리 조회·검색 및 메타데이터 관리

> Wave: 3
> 계층: application (`src/application/inventory_query.py`, `metadata_service.py`)
> 담당 요건: FR-4xx(조회·검색), FR-6xx(메타데이터), FR-501·502·503(품질 표시 연계)
> 의존: 02, 06, 09
> 관련 결정: D-006, D-007

## 1. 목적

저장소에 적재된 인벤토리를 조회·검색하고, 포탈 부여 메타데이터를 관리하는 유스케이스를 구현한다.

**하이퍼바이저를 직접 호출하지 않는다** (D-007). 모든 조회는 저장소를 경유한다.
**하이퍼바이저 분기(`if kind == VCENTER`)를 이 계층에 작성하지 않는다.**

## 2. 조회 유스케이스 (FR-401·402·406)

```python
@dataclass(frozen=True)
class Page:
    offset: int = 0
    limit: int = 50          # 최대 500 제한
    sort_by: str = "name"
    sort_desc: bool = False

@dataclass(frozen=True)
class PagedResult[T]:
    items: tuple[T, ...]
    total: int
    page: Page

class InventoryQueryService:
    async def list_virtual_machines(
        self, scope: AccessScope, criteria: SearchCriteria, page: Page
    ) -> PagedResult[VmSummary]: ...

    async def get_virtual_machine(
        self, scope: AccessScope, resource_id: UUID
    ) -> VmDetail: ...
```

**목록과 상세를 다른 모델로 분리한다.**
목록에 전체 속성을 실으면 5,000건 조회 시 응답이 수십 MB가 된다.

| 모델 | 포함 | 용도 |
|---|---|---|
| `VmSummary` | 이름, 전원 상태, IP(대표 1개), OS, vCPU, 메모리, 호스트, 연결명, 신선도, 소유자 | 목록(FR-401) |
| `VmDetail` | `spec.md` §2.2 전체 + 디스크·어댑터·스냅샷 + 메타데이터 + 관계 | 상세(FR-402) |

**조회 범위 적용**: 모든 메서드가 `scope`를 **첫 번째 필수 인자**로 받는다 (계획 09 §3.1).

## 3. 검색 (FR-403·404·405)

### 3.1 IP 역조회 (FR-404) — 최다 사용 시나리오

장애 대응 시 "이 IP가 어느 VM인가"를 즉시 답해야 한다. **전용 경로로 최적화한다.**

```python
async def find_by_ip(self, scope: AccessScope, ip: str) -> list[VmSummary]:
    """IP로 VM을 찾는다. 정확 일치 우선, 부분 일치는 별도 옵션."""
```

- `vm_adapter_ips.ip_address`(INET, 인덱스) 정확 일치 조회 (계획 06 §2.3)
- **결과가 복수일 수 있다**: 같은 IP가 다른 네트워크에 존재하거나, 미발견 자원이 과거 IP를 보유한 경우.
  `lifecycle` 상태를 함께 반환하여 UI가 구분 표시하게 한다
- 입력 정규화: 공백 제거, IPv6 축약형 정규화 (`src/utils/net.py`)
- **목표 응답 1초 이내** (NFR-101)

### 3.2 통합 검색 (FR-403)

단일 키워드로 이름·호스트명·IP·MAC·OS·소유자를 검색한다.

```python
async def search(self, scope: AccessScope, keyword: str, page: Page) -> PagedResult[VmSummary]:
```

입력 형태에 따라 검색 경로를 분기한다. **모든 컬럼에 LIKE를 거는 방식은 인덱스를 못 타 느리다.**

```
입력이 IP 형식        → vm_adapter_ips 정확 일치
입력이 MAC 형식       → vm_adapters.mac_address 정확 일치
그 외                 → name, guest_hostname에 pg_trgm 유사 검색
                        + 소유자·OS는 별도 조건으로 OR
```

### 3.3 다중 조건 필터 (FR-405)

```python
@dataclass(frozen=True)
class SearchCriteria:
    connection_ids: frozenset[UUID] | None = None
    hypervisor_kinds: frozenset[HypervisorKind] | None = None
    cluster_ids: frozenset[str] | None = None
    host_ids: frozenset[str] | None = None
    power_states: frozenset[PowerState] | None = None
    guest_os_contains: str | None = None
    environments: frozenset[Environment] | None = None
    owners: frozenset[str] | None = None
    tags: frozenset[str] | None = None
    lifecycles: frozenset[ResourceLifecycle] = frozenset({ResourceLifecycle.ACTIVE})
    guest_availability: frozenset[GuestInfoAvailability] | None = None   # FR-504
    stale_before: datetime | None = None                                  # FR-502
```

**`lifecycles` 기본값이 `ACTIVE`인 것이 중요하다.** 미발견·폐기·연결 해제된 자원이 기본 목록에 섞이면
운영자가 존재하지 않는 VM을 실재한다고 오인한다. 명시 요청 시에만 포함한다.

## 4. 관계 탐색 (FR-306·409)

자원 간 참조는 `native_id` 문자열로 저장되어 있다 (계획 06 §5.1). 조회 시점에 해석한다.

```python
async def get_related(self, scope: AccessScope, resource_id: UUID) -> RelatedResources:
    """VM의 소속 호스트·클러스터·데이터스토어·네트워크를 조회한다."""

async def list_vms_on_host(self, scope, host_id: UUID, page: Page) -> PagedResult[VmSummary]: ...
async def list_vms_on_datastore(self, scope, datastore_id: UUID, page: Page) -> ...: ...
```

**양방향 조회를 지원한다**: VM → 호스트, 호스트 → VM 목록.

참조 대상이 없을 수 있다(호스트 수집 실패, 아직 미수집). 이때 **예외를 던지지 말고** 참조 이름만 반환한다.

## 5. 메타데이터 관리 (FR-6xx)

```python
class MetadataService:
    async def get(self, scope, resource_id: UUID) -> ResourceMetadata | None: ...
    async def update(self, scope, actor: str, resource_id: UUID, patch: MetadataPatch) -> ResourceMetadata: ...
    async def bulk_update(self, scope, actor: str, resource_ids: list[UUID], patch: MetadataPatch) -> int: ...
    async def list_missing_metadata(self, scope, page: Page) -> PagedResult[VmSummary]: ...   # FR-503
```

### 5.1 보존 규칙 (FR-602)

메타데이터는 `resource_metadata` 테이블에만 쓴다 (계획 06 §3.2).
**이 서비스는 수집 테이블에 대한 쓰기 권한을 갖지 않는다** — 저장소 인터페이스를 분리하여 구조적으로 보장한다.

### 5.2 부분 갱신

```python
@dataclass(frozen=True)
class MetadataPatch:
    """미지정 필드는 변경하지 않는다. None으로 지우려면 UNSET 센티널 사용."""
    owner: str | None | UnsetType = UNSET
    environment: Environment | None | UnsetType = UNSET
    ...
```

`None`(값 지움)과 "미지정"(변경 안 함)을 구분해야 한다. 구분하지 않으면 일부 필드만 수정할 때 나머지가 지워진다.

### 5.3 감사·이력

- 모든 변경을 감사 로그에 기록 (계획 10, `METADATA_UPDATE`, 이전값 → 새값)
- 변경 이력(계획 12)에도 기록하여 자원 타임라인에 표시

### 5.4 일괄 가져오기 (FR-605, Should)

Excel/CSV로 메타데이터를 일괄 등록한다. 매칭 키는 **VM 이름이 아니라 자원 ID 또는 (연결명 + VM 이름)** 을 쓴다.
이름은 중복될 수 있어 잘못된 자원에 메타데이터가 붙는다.

- 가져오기 전 **미리보기**(몇 건 매칭, 몇 건 미매칭)를 제공하고 확인 후 반영
- 미매칭 행은 사유와 함께 결과로 반환

## 6. 데이터 품질 조회 (FR-501~503)

품질 판정 로직은 계획 12에 있고, 이 계획은 조회 인터페이스를 제공한다.

```python
async def list_unavailable_guest_info(self, scope, page) -> PagedResult[VmSummary]: ...  # FR-504
async def list_stale_resources(self, scope, threshold: timedelta, page) -> ...: ...      # FR-502
async def list_missing_metadata(self, scope, page) -> ...: ...                           # FR-503
```

## 7. 성능 고려 (NFR-101·102)

| 항목 | 방법 |
|---|---|
| 목록 조회 1초 (5,000건 기준) | `VmSummary` 전용 쿼리, 필요한 컬럼만 SELECT, 인덱스 사용 |
| 총 건수 | `COUNT(*)`가 느려지면 근사치 또는 상한 표시로 전환 |
| N+1 방지 | 어댑터·디스크는 상세에서만 조회. 목록은 집계 컬럼(계획 06 §2.2) 사용 |
| 캐시 | 대시보드 집계만 Redis 캐시(TTL 5분). **자원 목록은 캐시하지 않는다** — 범위 필터가 사용자마다 달라 캐시 키가 폭발한다 |

## 8. 구현 순서

1. `SearchCriteria`·`Page`·`PagedResult` → 검증: 기본값(`lifecycles=ACTIVE`) 확인
2. `VmSummary`/`VmDetail` 모델 + 저장소 쿼리 → 검증: 필요한 컬럼만 조회하는지
3. `list_virtual_machines` + 범위 필터 → 검증: **범위 밖 자원 미노출**
4. `find_by_ip` → 검증: 인덱스 사용(`EXPLAIN`), 복수 결과 처리, 1초 이내
5. `search` 입력 분기 → 검증: IP/MAC/일반 키워드별 경로
6. 다중 조건 필터 → 검증: 조합 필터 정확도
7. 관계 탐색 → 검증: 양방향 조회, 참조 대상 부재 시 정상 동작
8. `MetadataService` → 검증: **재수집 후 메타데이터 보존**, 부분 갱신 시 타 필드 유지
9. 품질 조회 3종

## 9. 완료 기준

- [ ] 모든 조회 메서드가 `scope`를 필수 인자로 받고 SQL에 반영
- [ ] IP 역조회가 5,000건 기준 1초 이내, 인덱스 스캔 확인
- [ ] 기본 목록에 미발견·폐기 자원이 포함되지 않음
- [ ] vCenter/Hyper-V 자원이 동일 `VmSummary` 포맷으로 반환됨
- [ ] 메타데이터 부분 갱신 시 미지정 필드가 유지됨
- [ ] 메타데이터 변경이 감사 로그·변경 이력에 기록됨
- [ ] `arch_check.py` 통과 — **어댑터 직접 import 없음**
- [ ] 유스케이스에 하이퍼바이저 분기가 없음

## 10. 주의사항

- **범위 필터 누락**이 가장 위험한 결함이다. 새 조회 메서드를 추가할 때마다 확인한다 (계획 09 §9).
- 목록 모델에 상세 정보를 넣고 싶은 유혹이 있으나, 응답 크기와 N+1이 성능을 무너뜨린다.
- `MetadataPatch`의 UNSET 센티널을 빠뜨리면 부분 수정 시 데이터가 지워진다. Pydantic의 `model_fields_set`을 활용해도 된다.
- 일괄 가져오기에서 이름 기반 매칭은 사고를 부른다 (§5.4).
