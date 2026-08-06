# 12. 변경 이력·수명주기·데이터 품질

> Wave: 3 · 계층: application (`change_history.py`) · domain (`history.py`)
> 담당 요건: FR-7xx, FR-501~505, FR-307·308
> 의존: 02, 06 · 관련 결정: D-006

## 1. 목적

수집 결과를 비교해 속성 변경을 감지·기록하고, 신규·삭제 자원을 추적하며, 데이터 품질을 판정한다.

`docs/00_research_notes.md` §9: 변경 추적은 **무엇이 언제 어떻게 바뀌었는지**를 남겨 감사 추적과 문제 해결을 지원한다.

## 2. 도메인 모델 (`src/domain/history.py`)

```python
class ChangeType(StrEnum):
    CREATED = "created"          # 신규 발견 (FR-703)
    UPDATED = "updated"          # 속성 변경 (FR-701)
    MISSING = "missing"          # 수집에서 사라짐 (FR-704)
    RESTORED = "restored"        # 다시 발견됨
    RETIRED = "retired"          # 유예 경과 후 폐기
    DISCONNECTED = "disconnected"  # 연결 삭제 (FR-109)
    METADATA = "metadata"        # 포탈 메타데이터 변경 (사용자 행위)


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str                   # 도메인 필드 경로 (예: "guest.ipv4_addresses")
    old_value: str | None        # 표시용 문자열로 정규화
    new_value: str | None


@dataclass(frozen=True, slots=True)
class ResourceChange:
    change_id: UUID
    resource_id: UUID
    resource_type: ResourceType
    detected_at: datetime
    change_type: ChangeType
    field_changes: tuple[FieldChange, ...] = ()
    run_id: UUID | None = None   # 수집 변경이면 수집 ID
    actor: str | None = None     # 사용자 변경이면 사용자 ID (시스템이면 None)

    @property
    def is_user_action(self) -> bool:
        return self.actor is not None
```

---

## 3. 변경 감지 (FR-701) — 이 계획의 핵심

### 3.1 감지 시점

**저장소 upsert 시점에 비교한다.** 저장 후 별도 배치로 돌리면 이전 값을 이미 잃은 뒤다.

```
저장소 upsert (계획 06 §3.1):
  1. CI 식별 규칙으로 기존 레코드 조회
  2. 게스트 정보 병합 (_merge_guest — §3.4)
  3. diff_virtual_machine(previous, merged) 호출        ← 이 계획
  4. 자원 레코드 갱신
  5. UpsertResult.changes로 반환 → 이 계층이 이력 기록
```

### 3.2 추적 대상 필드 (FR-702)

```python
@dataclass(frozen=True, slots=True)
class TrackedField:
    name: str                                   # 표시명
    extract: Callable[[VirtualMachine], Any]    # 값 추출
    render: Callable[[Any], str | None]         # 표시 문자열 변환
    compare: Callable[[Any, Any], bool] | None = None    # 커스텀 비교


TRACKED_VM_FIELDS: tuple[TrackedField, ...] = (
    TrackedField("name",            lambda v: v.name,                    _s),
    TrackedField("power_state",     lambda v: v.power_state,             _s),
    TrackedField("vcpu_count",      lambda v: v.cpu.total_vcpu,          _s),
    TrackedField("memory_mb",       lambda v: v.memory.assigned_mb,      _s),
    TrackedField("total_disk_bytes",lambda v: v.total_provisioned_bytes, _s,
                 compare=_bytes_close),                                  # §3.3
    TrackedField("host",            lambda v: v.host_native_id,          _s),
    TrackedField("cluster",         lambda v: v.cluster_native_id,       _s),
    TrackedField("guest_os_name",   lambda v: v.guest.os_name,           _s),
    TrackedField("guest_hostname",  lambda v: v.guest.hostname,          _s),
    TrackedField("ipv4_addresses",  lambda v: v.guest.ipv4_addresses,    _join,
                 compare=_set_equal),                                    # §3.3
    TrackedField("mac_addresses",   lambda v: v.mac_addresses,           _join,
                 compare=_set_equal),
    TrackedField("guest_availability", lambda v: v.guest.availability,   _s),
)
```

**추적하지 않는 것** — 노이즈가 이력을 무용지물로 만든다:

| 제외 필드 | 이유 |
|---|---|
| `last_seen_at` | 매 수집마다 변경 |
| `boot_time` / 가동 시간 | 연속 변동 |
| `total_used_bytes` | Thin 디스크는 계속 증가 |
| `snapshot_size_bytes` | 연속 변동 |
| `connection_state` | 일시적 변동이 잦음 |

### 3.3 비교 규칙

```python
def _set_equal(a: Sequence[str] | None, b: Sequence[str] | None) -> bool:
    """순서 무관 비교. IP·MAC 목록의 순서 변화는 변경이 아니다."""
    return set(a or ()) == set(b or ())


BYTES_TOLERANCE = 1024 * 1024        # 1MB

def _bytes_close(a: int | None, b: int | None) -> bool:
    """용량은 반올림 오차를 무시한다."""
    if a is None or b is None:
        return a == b
    return abs(a - b) < BYTES_TOLERANCE


def _join(v: Sequence[str] | None) -> str | None:
    return ", ".join(sorted(v)) if v else None


def _s(v: Any) -> str | None:
    return None if v is None else str(v)
```

### 3.4 수집 불가 전환 — 가장 흔한 오탐 (FR-501)

```
이전: guest_availability=AVAILABLE, ip=[10.0.0.5], os="Ubuntu 22.04"
현재: guest_availability=TOOLS_NOT_RUNNING, ip=[], os=None

잘못된 기록:  ip: 10.0.0.5 → (없음),  os: Ubuntu 22.04 → (없음)     ← 오탐
올바른 기록:  guest_availability: available → tools_not_running     ← 이것만
```

VMware Tools가 잠깐 멈추면 전체 VM에 "IP 사라짐" 이력이 대량 생성되어 이력 자체가 신뢰를 잃는다.

**두 겹으로 막는다**:

1. **저장소의 `_merge_guest`**가 이전 게스트 값을 유지한다 (계획 06 §3.3).
   `GuestInfo.with_fallback`이 이전 IP·OS를 그대로 가져오므로 diff에서 차이가 나지 않는다.
2. **diff 함수가 방어적으로 한 번 더 확인**한다.

```python
def diff_virtual_machine(previous: VirtualMachine, current: VirtualMachine) -> list[FieldChange]:
    """추적 대상 필드의 변경을 추출한다."""
    guest_became_unavailable = (
        previous.guest.is_collected and not current.guest.is_collected
    )

    changes: list[FieldChange] = []
    for f in TRACKED_VM_FIELDS:
        # 게스트 정보가 수집 불가로 전환된 경우, 게스트 파생 필드는 비교하지 않는다.
        # 도구가 멈춘 것이지 VM의 IP·OS가 없어진 것이 아니다.
        if guest_became_unavailable and f.name in GUEST_DERIVED_FIELDS:
            continue

        old, new = f.extract(previous), f.extract(current)
        equal = f.compare(old, new) if f.compare else (old == new)
        if not equal:
            changes.append(FieldChange(field=f.name, old_value=f.render(old), new_value=f.render(new)))
    return changes


GUEST_DERIVED_FIELDS = frozenset({
    "guest_os_name", "guest_hostname", "ipv4_addresses",
})
# guest_availability는 제외 대상이 아니다 — 이 변경은 기록해야 한다
```

### 3.5 전원 상태 노이즈 (FR-702 주의)

개발·테스트 VM은 매일 켜고 꺼진다. 전원 상태 변경이 이력의 대부분을 차지할 수 있다.

- **기록은 한다** (FR-702 요건)
- **조회에서 필터를 제공**하고, UI 타임라인 기본값에서 전원 상태만 있는 변경을 접어둔다 (계획 11)
- 리포트(계획 13 §6)의 "장기 전원 오프" 판정에 `power_state_changed_at`을 쓰므로 기록이 필요하다

---

## 4. 수명주기 전이 (FR-703·704)

```
(없음) ──────► ACTIVE          CREATED
ACTIVE ──────► MISSING         MISSING
MISSING ─────► ACTIVE          RESTORED
MISSING ─────► RETIRED         RETIRED
ACTIVE ──────► DISCONNECTED    DISCONNECTED
```

```python
class ChangeHistoryService:
    async def record(self, changes: Sequence[ResourceChange], run_id: UUID) -> int:
        """수집 중 발생한 변경을 일괄 기록한다."""
        if not changes:
            return 0
        rows = [_to_row(c, run_id) for c in changes]
        await self._repo.bulk_insert(rows)
        return len(rows)

    async def record_lifecycle(
        self, resource_ids: Sequence[UUID], resource_type: ResourceType,
        change_type: ChangeType, at: datetime, run_id: UUID | None = None,
    ) -> int:
        """수명주기 전이를 일괄 기록한다 (mark_missing / retire 배치에서 호출)."""


    async def list_new_resources(self, scope, since: datetime, page: Page) -> PagedResult[ResourceChange]: ...
    async def list_missing_resources(self, scope, since: datetime, page: Page) -> PagedResult[ResourceChange]: ...
```

### 4.1 대량 전이 시 이력 폭증 방지

연결 삭제(FR-109)로 수천 건이 `DISCONNECTED`가 되면 이력도 수천 건이 생긴다.

**자원별 이력 대신 요약 이력 1건 + 영향 건수**로 기록한다.

```python
async def record_bulk_lifecycle(
    self, connection_id: UUID, change_type: ChangeType, affected: int, at: datetime, actor: str | None
) -> None:
    """연결 단위 대량 전이는 요약으로 기록한다."""
    await self._repo.insert(ResourceChange(
        change_id=uuid4(), resource_id=connection_id,     # 연결을 대상으로
        resource_type=ResourceType.VIRTUAL_MACHINE,       # 대표 유형
        detected_at=at, change_type=change_type,
        field_changes=(FieldChange(field="affected_count", old_value=None, new_value=str(affected)),),
        actor=actor,
    ))
```

단, `MISSING` 전이는 자원별로 기록한다. 개별 자원의 소실 시점이 조사에 필요하기 때문이다.
다만 §4.2의 임계값을 넘으면 요약으로 전환한다.

### 4.2 대량 미발견 (FR-111)

계획 06 §4.3이 감지한 대량 미발견은 **자원별 이력을 만들지 않는다.**

```python
BULK_CHANGE_THRESHOLD = 100

if missing_result.marked >= BULK_CHANGE_THRESHOLD:
    await self.record_bulk_lifecycle(connection_id, ChangeType.MISSING, missing_result.marked, at, None)
else:
    await self.record_lifecycle(missing_ids, rtype, ChangeType.MISSING, at, run_id)
```

---

## 5. 타임라인 조회 (FR-705)

```python
async def get_timeline(
    self, scope: AccessScope, resource_id: UUID,
    since: datetime | None = None,
    change_types: frozenset[ChangeType] | None = None,
    exclude_power_only: bool = True,          # UI 기본값
    page: Page = Page(),
) -> PagedResult[ResourceChange]:
    await self._assert_in_scope(scope, resource_id)
    ...
```

```sql
SELECT change_id, resource_id, resource_type, detected_at, change_type,
       field_changes, run_id, actor
FROM resource_changes
WHERE resource_id = :resource_id
  AND (:since IS NULL OR detected_at >= :since)
  AND (:types IS NULL OR change_type = ANY(:types))
  AND (NOT :exclude_power_only OR NOT (
        change_type = 'updated'
        AND jsonb_array_length(field_changes) = 1
        AND field_changes->0->>'field' = 'power_state'
      ))
ORDER BY detected_at DESC, change_id
LIMIT :limit OFFSET :offset;
```

**페이징 필수.** 장수명 VM은 이력이 수천 건 쌓인다.

---

## 6. DB 스키마

```sql
CREATE TABLE resource_changes (
    change_id     UUID PRIMARY KEY,
    resource_id   UUID NOT NULL,
    resource_type TEXT NOT NULL,
    connection_id UUID NOT NULL,               -- 조회 범위 필터용 (비정규화)
    detected_at   TIMESTAMPTZ NOT NULL,
    change_type   TEXT NOT NULL,
    field_changes JSONB NOT NULL DEFAULT '[]',
    run_id        UUID,
    actor         TEXT
);

CREATE INDEX idx_changes_resource_time ON resource_changes (resource_id, detected_at DESC);
CREATE INDEX idx_changes_time_type ON resource_changes (detected_at DESC, change_type);
CREATE INDEX idx_changes_conn_time ON resource_changes (connection_id, detected_at DESC);
CREATE INDEX idx_changes_run ON resource_changes (run_id);
```

**`connection_id`를 비정규화 저장하는 이유**: 조회 범위 필터(FR-1003)를 걸 때 자원 테이블을 조인하지 않기 위함이다.
이력 테이블은 가장 커지므로 조인 비용이 크다.

`field_changes`를 JSONB로 두는 이유: 필드별 행으로 정규화하면 행 수가 폭증하고, 한 시점의 변경을 묶어 보기 어렵다.
필드 단위 검색이 필요해지면 GIN 인덱스를 추가한다.

### 6.1 보존 기간 (FR-706) — `[TODO]`

미확정. 임시 기본값 **1년** (`history_retention_days=365`).

```python
async def purge_old_history(self, retention: timedelta, now: datetime, batch: int = 10_000) -> int:
    """보존 기간 경과 이력을 배치로 삭제한다.

    한 번에 지우면 락이 오래 잡히므로 배치로 나눈다.
    """
    cutoff = now - retention
    total = 0
    while True:
        stmt = text("""
            DELETE FROM resource_changes
            WHERE change_id IN (
                SELECT change_id FROM resource_changes
                WHERE detected_at < :cutoff LIMIT :batch
            )
        """)
        deleted = (await self._session.execute(stmt, {"cutoff": cutoff, "batch": batch})).rowcount
        total += deleted
        if deleted < batch:
            break
    logger.info("변경 이력 정리 완료", extra={"deleted": total, "cutoff": cutoff.isoformat()})
    return total
```

**아카이브 여부는 정책 확정 시 결정**한다. 그 전까지는 삭제만 하되 실행 결과를 로그로 남긴다.
감사 로그(계획 10)와는 보존 정책이 다를 수 있으므로 설정 키를 분리한다.

---

## 7. 데이터 품질 판정 (FR-501~505)

```python
@dataclass(frozen=True, slots=True)
class DataQuality:
    guest_info_available: bool
    tool_status: GuestInfoAvailability
    is_stale: bool
    stale_reason: StaleReason | None
    has_required_metadata: bool
    missing_metadata_fields: tuple[str, ...]


class StaleReason(StrEnum):
    CONNECTION_FAILING = "connection_failing"    # 연결이 수집 실패 중
    CONNECTION_INACTIVE = "connection_inactive"  # 관리자가 비활성화
    CREDENTIAL_ERROR = "credential_error"
    RESOURCE_MISSING = "resource_missing"        # 자원 자체가 미발견
    UNKNOWN = "unknown"
```

### 7.1 신선도 판정 (FR-502)

**자원 개별 문제와 연결 문제를 구분한다.** 연결이 실패 중이면 그 연결의 모든 자원이 오래된 것이 당연하다.

```python
def assess_quality(
    vm: VmSummary, connection: ConnectionSummary, metadata: ResourceMetadata | None,
    threshold: timedelta, now: datetime,
) -> DataQuality:
    is_stale = (now - vm.last_seen_at) > threshold
    reason: StaleReason | None = None
    if is_stale:
        reason = {
            ConnectionStatus.CREDENTIAL_ERROR: StaleReason.CREDENTIAL_ERROR,
            ConnectionStatus.INACTIVE: StaleReason.CONNECTION_INACTIVE,
            ConnectionStatus.UNREACHABLE: StaleReason.CONNECTION_FAILING,
            ConnectionStatus.PERMISSION_ERROR: StaleReason.CONNECTION_FAILING,
        }.get(connection.status)
        if reason is None:
            reason = (StaleReason.RESOURCE_MISSING
                      if vm.lifecycle is not ResourceLifecycle.ACTIVE
                      else StaleReason.UNKNOWN)

    missing_fields = []
    if metadata is None or not metadata.owner:
        missing_fields.append("owner")
    if metadata is None or metadata.environment is None:
        missing_fields.append("environment")

    return DataQuality(
        guest_info_available=(vm.guest_availability is GuestInfoAvailability.AVAILABLE),
        tool_status=vm.guest_availability,
        is_stale=is_stale, stale_reason=reason,
        has_required_metadata=not missing_fields,
        missing_metadata_fields=tuple(missing_fields),
    )
```

UI는 연결 단위 문제를 상단 배너로, 자원 단위 문제만 행에 표시한다 (계획 11 §3.3).

### 7.2 품질 지표 (FR-505)

```sql
SELECT
    COUNT(*)                                                        AS total,
    COUNT(*) FILTER (WHERE guest_availability = 'available')        AS guest_ok,
    COUNT(*) FILTER (WHERE md.owner IS NOT NULL
                       AND md.environment IS NOT NULL)              AS metadata_ok,
    COUNT(*) FILTER (WHERE vm.last_seen_at < :stale_cutoff)         AS stale,
    COUNT(*) FILTER (WHERE guest_availability = 'tools_not_installed') AS tools_missing,
    COUNT(*) FILTER (WHERE guest_availability = 'tools_not_running')   AS tools_stopped
FROM virtual_machines vm
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids));
```

**조회 범위 반영 필수** (계획 09 §9). Redis 캐시 TTL 5분, **범위별 키 분리**.

---

## 8. 중복 후보 (FR-308)

계획 06 §3.1이 감지한 후보를 조회·해소한다.

```sql
CREATE TABLE duplicate_candidates (
    candidate_id      UUID PRIMARY KEY,
    existing_resource_id UUID NOT NULL,
    existing_connection_id UUID NOT NULL,
    incoming_connection_id UUID NOT NULL,
    incoming_resource_id UUID,
    matched_rule      SMALLINT NOT NULL,
    matched_value     TEXT NOT NULL,
    detected_at       TIMESTAMPTZ NOT NULL,
    dismissed_at      TIMESTAMPTZ,
    dismissed_by      TEXT,
    dismiss_reason    TEXT,
    UNIQUE (existing_resource_id, incoming_connection_id, matched_rule, matched_value)
);
```

`UNIQUE` 제약으로 같은 후보가 매 수집마다 중복 생성되는 것을 막는다.

```python
async def list_duplicate_candidates(self, scope, include_dismissed: bool, page) -> PagedResult[...]: ...
async def dismiss(self, scope, actor: str, candidate_id: UUID, reason: str) -> None: ...
```

**자동 병합하지 않는다** (D-006). 관리자가 확인하고 무시하거나 연결 설정을 조정한다.

---

## 9. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `ChangeType`·`FieldChange`·`ResourceChange` + 스키마 | JSONB 직렬화 왕복 |
| 2 | `TRACKED_VM_FIELDS` + 비교 함수 | **IP 순서 변경이 변경으로 기록되지 않음**, 용량 오차 허용 |
| 3 | `diff_virtual_machine` | 필드별 감지, 노이즈 필드 제외 |
| 4 | **수집 불가 전환 처리** | 도구 정지 시 IP·OS 소실 미기록, availability 변경만 기록 |
| 5 | 저장소 upsert 연동 | 2회 수집 중 변경분만 이력 생성 |
| 6 | 수명주기 전이 기록 | 5가지 전이 |
| 7 | 대량 전이 요약 | 임계값 초과 시 요약 1건 |
| 8 | 타임라인 조회 | 역순, 필터, `exclude_power_only`, 페이징 |
| 9 | 품질 판정 | 신선도 사유 구분, 메타데이터 누락 |
| 10 | 품질 집계 + 캐시 | 범위별 캐시 키 |
| 11 | 보존 정리 배치 | 배치 삭제, 로그 |
| 12 | 중복 후보 | UNIQUE 제약으로 중복 방지 |

## 10. 완료 기준

- [ ] FR-702 최소 추적 필드가 모두 감지됨
- [ ] IP·MAC 목록의 순서 변화가 변경으로 기록되지 않음
- [ ] **도구 미동작 전환 시 IP·OS 소실로 오탐되지 않음**
- [ ] 도구 미동작 시 마지막으로 알려진 게스트 값이 유지됨 (계획 06 `_merge_guest` 연계)
- [ ] `last_seen_at` 등 노이즈 필드가 이력에 없음
- [ ] 수명주기 5가지 전이가 모두 기록됨
- [ ] 대량 전이(100건 초과)가 요약 1건으로 기록됨
- [ ] 타임라인이 수집 변경과 사용자 변경을 `actor`로 구분
- [ ] 신선도 사유가 연결 문제와 자원 문제를 구분
- [ ] 품질 집계에 조회 범위가 반영됨
- [ ] 보존 정리가 배치로 동작하고 결과를 로그로 남김
- [ ] 중복 후보가 매 수집마다 중복 생성되지 않음

## 11. 주의사항

- **§3.4의 오탐이 이 계획에서 가장 흔한 결함이다.** 저장소의 `_merge_guest`와 diff의 방어를 둘 다 구현한다.
- 전원 상태 변경이 이력을 뒤덮지 않도록 조회 필터를 제공한다 (§3.5).
- 이력 테이블은 가장 빠르게 커진다. 인덱스와 보존 정책을 처음부터 넣는다.
- 변경 감지를 별도 배치로 돌리면 이전 값을 이미 잃은 뒤다. 반드시 upsert 시점에 비교한다.
- 대량 삭제는 배치로 나눈다. 한 번에 지우면 락이 오래 잡혀 수집이 멈춘다.
