# 07. 인벤토리 조회·검색 및 메타데이터 관리

> Wave: 3 · 계층: application (`src/application/`)
> 담당 요건: FR-4xx, FR-6xx, FR-501~503 조회 연계
> 의존: 02, 06, 09 · 관련 결정: D-006, D-007

## 1. 목적

저장소에 적재된 인벤토리를 조회·검색하고 포탈 메타데이터를 관리한다.

- **하이퍼바이저를 직접 호출하지 않는다** (D-007). 모든 조회는 저장소 경유
- **하이퍼바이저 분기(`if kind == VCENTER`)를 이 계층에 두지 않는다**
- **모든 조회 메서드는 `AccessScope`를 첫 필수 인자로 받는다** (계획 09 §3.1)

## 2. 조회 모델 (`src/application/dto.py`)

목록과 상세를 분리한다. 목록에 전체 속성을 실으면 5,000건 조회 시 응답이 수십 MB가 된다.

```python
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page:
    offset: int = 0
    limit: int = 50
    sort_by: str = "name"
    sort_desc: bool = False

    MAX_LIMIT: ClassVar[int] = 500
    ALLOWED_SORT: ClassVar[frozenset[str]] = frozenset({
        "name", "power_state", "guest_os_name", "vcpu_count",
        "memory_mb", "last_seen_at", "host_native_id",
    })

    def validate(self) -> None:
        if not (1 <= self.limit <= self.MAX_LIMIT):
            raise ValidationError(f"limit은 1~{self.MAX_LIMIT} 범위여야 합니다.", field="limit")
        if self.sort_by not in self.ALLOWED_SORT:
            raise ValidationError(f"정렬할 수 없는 컬럼입니다: {self.sort_by}", field="sort_by")


@dataclass(frozen=True, slots=True)
class PagedResult(Generic[T]):
    items: tuple[T, ...]
    total: int
    page: Page
    total_is_estimate: bool = False       # 대량 시 근사치 (§7.2)
```

**`ALLOWED_SORT` 화이트리스트가 필수다.** 정렬 컬럼을 문자열로 받아 SQL에 넣으면 인젝션 경로가 된다.

```python
@dataclass(frozen=True, slots=True)
class VmSummary:
    """목록용 (FR-401). 조인 없이 virtual_machines + metadata만으로 구성한다."""
    resource_id: UUID
    name: str
    connection_id: UUID
    connection_name: str
    hypervisor: HypervisorKind
    power_state: PowerState
    primary_ip: str | None
    guest_availability: GuestInfoAvailability     # 수집 불가 판정 (FR-501)
    guest_os_name: str | None
    guest_os_source: OsSource | None
    vcpu_count: int | None
    memory_mb: int | None
    host_name: str | None
    cluster_name: str | None
    lifecycle: ResourceLifecycle
    last_seen_at: datetime
    is_stale: bool                                 # FR-502
    owner: str | None
    environment: Environment | None


@dataclass(frozen=True, slots=True)
class VmDetail:
    """상세용 (FR-402). spec.md §2.2 전체 + 하위 컬렉션 + 메타데이터 + 관계."""
    vm: VirtualMachine
    metadata: ResourceMetadata | None
    connection: ConnectionSummary
    host: ResourceRef | None
    cluster: ResourceRef | None
    datastores: tuple[ResourceRef, ...]
    networks: tuple[ResourceRef, ...]
    quality: DataQuality
    recent_changes: tuple[ResourceChange, ...]     # 최근 N건 (FR-705)
```

---

## 3. 조회 서비스 (`inventory_query.py`)

### 3.1 목록 조회

```python
class InventoryQueryService:
    def __init__(self, repo: InventoryReadRepository, settings: Settings) -> None: ...

    async def list_virtual_machines(
        self, scope: AccessScope, criteria: SearchCriteria, page: Page
    ) -> PagedResult[VmSummary]:
        page.validate()
        criteria.validate()
        return await self._repo.search_vms(scope, criteria, page)
```

### 3.2 SQL — 목록 쿼리

```sql
SELECT
    vm.resource_id, vm.name, vm.connection_id, c.display_name AS connection_name,
    c.kind AS connection_kind,
    vm.power_state, vm.guest_availability, vm.guest_os_name, vm.guest_os_source,
    vm.vcpu_count, vm.memory_mb, vm.lifecycle, vm.last_seen_at,
    vm.host_native_id, vm.cluster_native_id,
    h.name AS host_name, cl.name AS cluster_name,
    md.owner, md.environment,
    (SELECT host(ip.ip_address) FROM vm_adapter_ips ip
      WHERE ip.resource_id = vm.resource_id AND ip.family = 4
      ORDER BY ip.id LIMIT 1) AS primary_ip
FROM virtual_machines vm
JOIN connections c        ON c.connection_id = vm.connection_id
LEFT JOIN hosts h         ON h.connection_id = vm.connection_id
                          AND h.native_id = vm.host_native_id
LEFT JOIN clusters cl     ON cl.connection_id = vm.connection_id
                          AND cl.native_id = vm.cluster_native_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = ANY(:lifecycles)
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))    -- 조회 범위 (FR-1003)
  AND (:connection_ids IS NULL OR vm.connection_id = ANY(:connection_ids))
  AND (:power_states  IS NULL OR vm.power_state = ANY(:power_states))
  AND (:cluster_ids   IS NULL OR vm.cluster_native_id = ANY(:cluster_ids))
  AND (:host_ids      IS NULL OR vm.host_native_id = ANY(:host_ids))
  AND (:os_contains   IS NULL OR vm.guest_os_name ILIKE '%' || :os_contains || '%')
  AND (:environments  IS NULL OR md.environment = ANY(:environments))
  AND (:owners        IS NULL OR md.owner = ANY(:owners))
  AND (:guest_avail   IS NULL OR vm.guest_availability = ANY(:guest_avail))
  AND (:stale_before  IS NULL OR vm.last_seen_at < :stale_before)
ORDER BY {sort_column} {direction}, vm.resource_id
LIMIT :limit OFFSET :offset;
```

**`ORDER BY ... , vm.resource_id`**: 정렬 컬럼에 동일 값이 많으면 페이지 경계에서 행이 중복·누락된다.
고유 컬럼을 타이브레이커로 반드시 추가한다.

**`primary_ip` 서브쿼리**: 목록에 IP를 보여줘야 하는데 어댑터를 조인하면 행이 곱해진다.
LATERAL 서브쿼리로 대표 1건만 가져온다.

### 3.3 필터 조건 (`SearchCriteria`)

```python
@dataclass(frozen=True, slots=True)
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
    guest_availability: frozenset[GuestInfoAvailability] | None = None
    stale_before: datetime | None = None
    lifecycles: frozenset[ResourceLifecycle] = frozenset({ResourceLifecycle.ACTIVE})

    def validate(self) -> None:
        if self.guest_os_contains is not None and len(self.guest_os_contains) < 2:
            raise ValidationError("OS 검색어는 2자 이상이어야 합니다.", field="guest_os_contains")
```

**`lifecycles` 기본값이 `ACTIVE`인 것이 중요하다.** 미발견·폐기·연결 해제 자원이 기본 목록에 섞이면
운영자가 존재하지 않는 VM을 실재한다고 오인한다.

---

## 4. 검색 (FR-403·404)

### 4.1 IP 역조회 (FR-404) — 최다 사용 시나리오

```python
async def find_by_ip(
    self, scope: AccessScope, raw_ip: str, include_inactive: bool = False
) -> list[VmSummary]:
    """IP로 VM을 찾는다. 장애 대응 시 가장 많이 쓰이는 경로다."""
    ip = normalize_ip(raw_ip)                       # 계획 02 §9.3
    if ip is None:
        raise ValidationError("올바른 IP 주소가 아닙니다.", field="ip")
    return await self._repo.find_vms_by_ip(scope, ip, include_inactive)
```

```sql
SELECT DISTINCT ON (vm.resource_id) <VmSummary 컬럼들>
FROM vm_adapter_ips ip
JOIN virtual_machines vm ON vm.resource_id = ip.resource_id
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE ip.ip_address = :ip                          -- INET 정확 일치, 인덱스 사용
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
  AND (:include_inactive OR vm.lifecycle = 'active')
ORDER BY vm.resource_id, vm.last_seen_at DESC;
```

**결과가 복수일 수 있다**:
- 같은 IP가 서로 다른 네트워크(분리망)에 존재
- 미발견 자원이 과거 IP를 보유
- DHCP 재할당으로 IP가 이동

`lifecycle`을 함께 반환하여 UI가 구분 표시하게 한다 (계획 11 §4).

**목표 응답 1초 이내** (NFR-101). `idx_adapter_ips_addr` 인덱스 사용을 `EXPLAIN`으로 확인한다.

### 4.2 통합 검색 (FR-403) — 입력 형태별 분기

**모든 컬럼에 LIKE를 거는 방식은 인덱스를 못 타 느리다.** 입력을 판별해 경로를 나눈다.

```python
async def search(self, scope: AccessScope, keyword: str, page: Page) -> PagedResult[VmSummary]:
    kw = keyword.strip()
    if not kw:
        raise ValidationError("검색어를 입력하세요.", field="keyword")

    if (ip := normalize_ip(kw)) is not None:
        items = await self._repo.find_vms_by_ip(scope, ip, include_inactive=False)
        return PagedResult(items=tuple(items[:page.limit]), total=len(items), page=page)

    if (mac := normalize_mac(kw)) is not None:
        return await self._repo.find_vms_by_mac(scope, mac, page)

    if _looks_like_ip_prefix(kw):                   # "10.0.1." 같은 부분 입력
        return await self._repo.find_vms_by_ip_prefix(scope, kw, page)

    return await self._repo.search_vms_by_text(scope, kw, page)
```

```sql
-- 텍스트 검색: pg_trgm 유사도 + 메타데이터 정확 일치
SELECT <VmSummary 컬럼들>,
       GREATEST(similarity(vm.name, :kw),
                similarity(COALESCE(vm.guest_hostname, ''), :kw)) AS score
FROM virtual_machines vm
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
  AND (
        vm.name % :kw                               -- pg_trgm 유사 (GIN 인덱스)
     OR vm.guest_hostname % :kw
     OR vm.guest_os_name ILIKE '%' || :kw || '%'
     OR md.owner ILIKE '%' || :kw || '%'
     OR md.service_name ILIKE '%' || :kw || '%'
  )
ORDER BY score DESC, vm.name, vm.resource_id
LIMIT :limit OFFSET :offset;
```

`%` 연산자의 임계값은 `pg_trgm.similarity_threshold`(기본 0.3)로 조정한다.
짧은 검색어에서 결과가 안 나오면 임계값을 낮춘다.

```python
def _looks_like_ip_prefix(kw: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){0,3}\.?", kw))
```

```sql
-- IP 접두 검색
WHERE ip.ip_address << :cidr        -- 예: '10.0.1.0/24'
```

---

## 5. 관계 탐색 (FR-306·409)

자원 간 참조는 `native_id` 문자열이다 (계획 06 §5.1). 조회 시점에 해석한다.

```python
async def get_related(self, scope: AccessScope, resource_id: UUID) -> RelatedResources:
    """VM의 소속 호스트·클러스터·데이터스토어·네트워크를 조회한다."""
```

```sql
-- VM이 사용하는 데이터스토어 (디스크 경로에서 역참조)
SELECT DISTINCT ds.resource_id, ds.name, ds.kind
FROM vm_disks d
JOIN datastores ds ON ds.connection_id = :connection_id
                  AND ds.name = d.datastore_name
WHERE d.resource_id = :resource_id;

-- VM이 연결된 네트워크
SELECT DISTINCT n.resource_id, n.name, n.kind, n.vlan_id
FROM vm_adapters a
JOIN networks n ON n.connection_id = :connection_id
               AND n.name = a.network_name
WHERE a.resource_id = :resource_id;
```

**참조 대상이 없을 수 있다** (호스트 수집 실패, 미수집, 이름 불일치).
이때 **예외를 던지지 말고** 이름만 담은 `ResourceRef(name=..., resource_id=None)`를 반환한다.

```python
@dataclass(frozen=True, slots=True)
class ResourceRef:
    name: str
    resource_id: UUID | None = None      # None이면 링크 불가 (미수집)
    kind: str | None = None
```

역방향 조회도 제공한다.

```python
async def list_vms_on_host(self, scope, host_resource_id: UUID, page: Page) -> PagedResult[VmSummary]: ...
async def list_vms_on_datastore(self, scope, ds_resource_id: UUID, page: Page) -> PagedResult[VmSummary]: ...
async def list_vms_on_network(self, scope, net_resource_id: UUID, page: Page) -> PagedResult[VmSummary]: ...
```

---

## 6. 메타데이터 관리 (`metadata_service.py`)

```python
class MetadataService:
    def __init__(
        self, repo: MetadataRepository, resources: InventoryReadRepository,
        audit: AuditService, history: ChangeHistoryService,
    ) -> None: ...

    async def update(
        self, scope: AccessScope, actor: str, resource_id: UUID, patch: MetadataPatch
    ) -> ResourceMetadata:
        await self._assert_in_scope(scope, resource_id)          # 범위 밖 자원 수정 차단
        before = await self._repo.get(resource_id)
        after = await self._repo.upsert(resource_id, patch, actor)

        diffs = _diff_metadata(before, after)
        if diffs:
            await self._audit.record(AuditEvent(
                actor=actor, action=AuditAction.METADATA_UPDATE,
                target_type="resource", target_id=str(resource_id),
                result="success",
                detail={"changed_fields": [d.field for d in diffs]},
            ))
            await self._history.record_metadata_change(resource_id, diffs, actor)
        return after
```

### 6.1 부분 갱신 (UNSET 처리)

```python
async def upsert(self, resource_id: UUID, patch: MetadataPatch, actor: str) -> ResourceMetadata:
    changed = patch.changed_fields()                  # UNSET 제외 (계획 02 §11)
    if not changed:
        return await self.get(resource_id) or ResourceMetadata(resource_id=resource_id)

    stmt = insert(ResourceMetadataRow).values(
        resource_id=resource_id, **changed, updated_by=actor, updated_at=func.now()
    ).on_conflict_do_update(
        index_elements=["resource_id"],
        set_={**changed, "updated_by": actor, "updated_at": func.now()},
    )
    await self._session.execute(stmt)
```

**`changed_fields()`가 UNSET을 걸러내므로** 미지정 필드는 SQL에 포함되지 않아 기존 값이 유지된다.

### 6.2 일괄 편집 (FR-604)

```python
async def bulk_update(
    self, scope: AccessScope, actor: str, resource_ids: Sequence[UUID], patch: MetadataPatch
) -> BulkResult:
    in_scope = await self._filter_in_scope(scope, resource_ids)
    skipped = len(resource_ids) - len(in_scope)
    ...
    return BulkResult(updated=len(in_scope), skipped=skipped)
```

**범위 밖 자원은 조용히 건너뛰지 말고 건수를 반환**한다. 사용자가 일부만 적용된 것을 알아야 한다.

### 6.3 일괄 가져오기 (FR-605)

매칭 키는 **자원 ID 또는 (연결명 + VM 이름)** 을 쓴다. 이름 단독 매칭은 중복 자원에 잘못 붙는다.

```python
async def preview_import(self, scope, rows: Sequence[ImportRow]) -> ImportPreview:
    """반영 전 매칭 결과를 미리 보여준다."""
    matched, ambiguous, not_found = [], [], []
    for row in rows:
        candidates = await self._resolve(scope, row)
        if len(candidates) == 1:
            matched.append((row, candidates[0]))
        elif len(candidates) > 1:
            ambiguous.append((row, candidates))       # 이름 중복
        else:
            not_found.append(row)
    return ImportPreview(matched=matched, ambiguous=ambiguous, not_found=not_found)
```

**확인 후 반영**한다. 미리보기 없이 바로 적용하면 잘못된 자원에 소유자가 붙는다.

---

## 7. 성능

### 7.1 인덱스 활용 확인

| 쿼리 | 기대 실행 계획 |
|---|---|
| IP 역조회 | `Index Scan on idx_adapter_ips_addr` |
| 이름 검색 | `Bitmap Index Scan on idx_vm_name_trgm` |
| 연결 필터 목록 | `Index Scan on idx_vm_conn_active` |

`EXPLAIN (ANALYZE, BUFFERS)`로 확인하고, Seq Scan이 나오면 인덱스나 쿼리를 조정한다.

### 7.2 총 건수

`COUNT(*)`는 대량에서 느리다.

```python
COUNT_EXACT_THRESHOLD = 10_000

async def _count(self, base_query) -> tuple[int, bool]:
    """상한까지만 정확히 세고, 초과하면 근사치로 전환한다."""
    limited = base_query.limit(COUNT_EXACT_THRESHOLD + 1)
    n = await self._scalar(select(func.count()).select_from(limited.subquery()))
    if n <= COUNT_EXACT_THRESHOLD:
        return n, False
    est = await self._estimate_rows(base_query)      # EXPLAIN 기반 추정
    return est, True
```

UI는 `total_is_estimate=True`면 `약 12,000건`처럼 표시한다.

### 7.3 캐시 정책

| 대상 | 캐시 | 이유 |
|---|---|---|
| 자원 목록·검색 | **하지 않음** | 조회 범위가 사용자마다 달라 캐시 키가 폭발 |
| 대시보드 집계 | Redis TTL 5분 | 범위별 키 분리 (`dashboard:{scope_hash}`) |
| 연결 목록 | Redis TTL 1분 | 관리자 전용, 변경 빈도 낮음 |

### 7.4 N+1 방지

목록 조회는 `virtual_machines`의 집계 컬럼(`disk_count`, `total_provisioned_bytes` 등, 계획 06 §2.3)을 쓴다.
디스크·어댑터 상세는 **상세 조회에서만** 로드한다.

---

## 8. 데이터 품질 조회 (FR-501~503)

판정 로직은 계획 12, 여기서는 조회 인터페이스만 제공한다.

```python
async def list_unavailable_guest_info(self, scope, page) -> PagedResult[VmSummary]:
    """도구 미설치·미동작 VM 목록 (FR-504)."""
    criteria = SearchCriteria(guest_availability=frozenset({
        GuestInfoAvailability.TOOLS_NOT_INSTALLED,
        GuestInfoAvailability.TOOLS_NOT_RUNNING,
    }))
    return await self.list_virtual_machines(scope, criteria, page)


async def list_stale_resources(self, scope, threshold: timedelta, page) -> PagedResult[VmSummary]:
    """신선도 초과 자원 (FR-502)."""
    criteria = SearchCriteria(stale_before=datetime.now(UTC) - threshold)
    return await self.list_virtual_machines(scope, criteria, page)
```

```sql
-- 필수 메타데이터 누락 (FR-503)
SELECT <VmSummary 컬럼들>
FROM virtual_machines vm
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
  AND (md.resource_id IS NULL OR md.owner IS NULL OR md.environment IS NULL)
ORDER BY vm.name, vm.resource_id
LIMIT :limit OFFSET :offset;
```

---

## 9. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `Page`·`SearchCriteria`·`PagedResult` | `ALLOWED_SORT` 화이트리스트, 기본 `lifecycles=ACTIVE` |
| 2 | `VmSummary`·`VmDetail` DTO | 목록에 하위 컬렉션이 없는지 |
| 3 | 목록 쿼리 + 범위 필터 | **범위 밖 자원 미노출**, 타이브레이커 정렬 |
| 4 | `find_by_ip` | Index Scan 확인, 복수 결과, 1초 이내 |
| 5 | `search` 입력 분기 | IP/MAC/접두/텍스트 4경로 |
| 6 | 다중 조건 필터 | 조합 정확도, NULL 파라미터 시 무시 |
| 7 | 관계 탐색 | 양방향, 참조 부재 시 `ResourceRef(resource_id=None)` |
| 8 | `MetadataService.update` | **부분 갱신 시 타 필드 유지**, 범위 검사 |
| 9 | 일괄 편집·가져오기 | 범위 밖 건수 반환, 미리보기 |
| 10 | 품질 조회 3종 | |
| 11 | 총 건수 근사 전환 | 상한 초과 시 `total_is_estimate` |

## 10. 완료 기준

- [ ] 모든 조회 메서드가 `scope`를 필수 인자로 받고 SQL에 반영
- [ ] 범위 밖 자원이 목록·상세·검색·관계 탐색 어디에도 노출되지 않음
- [ ] IP 역조회가 Index Scan, 5,000건 기준 1초 이내
- [ ] 정렬 컬럼이 화이트리스트로 제한됨
- [ ] 페이지 경계에서 행 중복·누락 없음 (타이브레이커)
- [ ] 기본 목록에 미발견·폐기 자원이 포함되지 않음
- [ ] vCenter/Hyper-V 자원이 동일 `VmSummary` 포맷으로 반환
- [ ] 메타데이터 부분 갱신 시 미지정 필드 유지
- [ ] 메타데이터 변경이 감사 로그·변경 이력에 기록
- [ ] 일괄 가져오기가 미리보기 후 반영
- [ ] `arch_check.py` 통과 — 어댑터 직접 import 없음, 하이퍼바이저 분기 없음

## 11. 주의사항

- **범위 필터 누락이 가장 위험한 결함이다.** 새 조회 메서드마다 확인한다 (계획 09 §9).
- 목록 모델에 상세 정보를 넣으면 응답 크기와 N+1이 성능을 무너뜨린다.
- `ORDER BY`에 타이브레이커를 빠뜨리면 페이징에서 행이 새거나 중복된다.
- `MetadataPatch`의 UNSET을 빠뜨리면 부분 수정 시 데이터가 지워진다.
- 일괄 가져오기의 이름 기반 매칭은 사고를 부른다 (§6.3).
- 정렬 컬럼을 사용자 입력 그대로 SQL에 넣지 않는다.
