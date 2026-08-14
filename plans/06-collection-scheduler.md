# 06. 저장소 스키마 및 수집 스케줄러

> Wave: 1 (Part A 저장소) · 3 (Part B 스케줄러)
> 계층: infrastructure (`db/`, `repository/`) · orchestration (`src/orchestration/`)
> 담당 요건: FR-2xx 전체, FR-303·305·307·308, FR-109·111·113·114, NFR-101·107·301~305
> 의존: 02, 03, 10 · 관련 결정: D-006, D-007, D-008

## 1. 목적

수집 인벤토리를 저장하고 연결별 주기 수집을 실행한다.
**CI 식별 규칙(FR-302)을 실제로 강제하는 지점**이며, 중복 생성·메타데이터 소실·부분 실패 처리의 성패가 여기서 갈린다.

---

# Part A. 저장소 (Wave 1)

## 2. DDL

### 2.1 확장과 공통

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 이름·호스트명 유사 검색 (FR-403)
```

### 2.2 connections

```sql
CREATE TABLE connections (
    connection_id       UUID PRIMARY KEY,
    kind                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    description         TEXT,
    address             TEXT NOT NULL,
    port                INTEGER NOT NULL,
    protocol            TEXT NOT NULL DEFAULT 'https',
    auth_method         TEXT,                       -- Hyper-V만 사용
    session_configuration TEXT,                     -- JEA 엔드포인트 이름 (계획 05 §4.3.1)
    username            TEXT NOT NULL,
    password_encrypted  TEXT NOT NULL,              -- {key_version}:{nonce}:{ciphertext} (계획 10)
    verify_tls          BOOLEAN NOT NULL DEFAULT true,
    collection_interval_minutes INTEGER NOT NULL DEFAULT 360,
    collectable_types   TEXT[],                     -- NULL = 전체 (FR-208)
    status              TEXT NOT NULL DEFAULT 'active',
    last_success_at     TIMESTAMPTZ,
    last_attempt_at     TIMESTAMPTZ,
    last_error          TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_connection_target UNIQUE (address, username)   -- 중복 등록 방지 (FR-105)
);
```

`UNIQUE (address, username)`이 FR-105를 DB 레벨에서 강제한다. 애플리케이션 검증만으로는 동시 요청에서 뚫린다.

### 2.3 virtual_machines

```sql
CREATE TABLE virtual_machines (
    resource_id         UUID PRIMARY KEY,
    connection_id       UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id           TEXT NOT NULL,
    bios_uuid           TEXT,
    name                TEXT NOT NULL,
    power_state         TEXT NOT NULL DEFAULT 'unknown',
    connection_state    TEXT NOT NULL DEFAULT 'unknown',
    boot_time           TIMESTAMPTZ,
    -- CPU / 메모리
    vcpu_count          INTEGER,
    socket_count        INTEGER,
    cores_per_socket    INTEGER,
    memory_mb           BIGINT,
    dynamic_memory      BOOLEAN NOT NULL DEFAULT false,
    dynamic_min_mb      BIGINT,
    dynamic_max_mb      BIGINT,
    -- 플랫폼
    hw_version          TEXT,
    firmware            TEXT,
    generation          INTEGER,
    configured_os       TEXT,
    -- 게스트 (도구 의존 — FR-501)
    guest_availability  TEXT NOT NULL DEFAULT 'unknown',
    guest_os_name       TEXT,
    guest_os_version    TEXT,
    guest_os_source     TEXT,                       -- guest_tools | vm_config (FR-304)
    guest_hostname      TEXT,
    guest_tool_version  TEXT,
    guest_observed_at   TIMESTAMPTZ,                -- 게스트 값이 실제 수집된 시각
    -- 관계 (논리 참조 — FK 아님, §5.1)
    host_native_id      TEXT,
    cluster_native_id   TEXT,
    resource_pool       TEXT,
    folder_path         TEXT,
    -- 집계 (목록 조회 성능 — 원본은 vm_disks)
    total_provisioned_bytes BIGINT,
    total_used_bytes    BIGINT,
    disk_count          INTEGER NOT NULL DEFAULT 0,
    adapter_count       INTEGER NOT NULL DEFAULT 0,
    snapshot_count      INTEGER NOT NULL DEFAULT 0,
    latest_snapshot_at  TIMESTAMPTZ,
    snapshot_size_bytes BIGINT,
    -- 기타
    annotation          TEXT,
    custom_attributes   JSONB,                      -- vCenter Custom Attributes (이름→값). Tags 아님 (D-010)
    created_at_hv       TIMESTAMPTZ,
    -- 수명주기
    lifecycle           TEXT NOT NULL DEFAULT 'active',
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    missing_since       TIMESTAMPTZ,
    power_state_changed_at TIMESTAMPTZ,             -- 유휴 판정용 (계획 13 §6)

    CONSTRAINT uq_vm_native UNIQUE (connection_id, native_id)    -- CI 식별 1순위
);
```

`UNIQUE (connection_id, native_id)`가 **CI 식별 1순위를 DB 제약으로 강제**한다.
애플리케이션 로직에 버그가 있어도 중복이 물리적으로 생기지 않는다 (FR-303).

### 2.4 VM 하위 컬렉션

```sql
CREATE TABLE vm_disks (
    id                  UUID PRIMARY KEY,
    resource_id         UUID NOT NULL REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    device_key          TEXT NOT NULL,
    label               TEXT,
    provisioned_bytes   BIGINT NOT NULL DEFAULT 0,
    used_bytes          BIGINT,
    provisioning        TEXT NOT NULL DEFAULT 'unknown',
    datastore_name      TEXT,
    file_path           TEXT,
    UNIQUE (resource_id, device_key)
);

CREATE TABLE vm_adapters (
    id                  UUID PRIMARY KEY,
    resource_id         UUID NOT NULL REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    device_key          TEXT NOT NULL,
    mac_address         TEXT,                       -- 정규화 형식 (소문자 콜론)
    adapter_type        TEXT,
    network_name        TEXT,
    connected           BOOLEAN,
    UNIQUE (resource_id, device_key)
);

CREATE TABLE vm_adapter_ips (
    id                  BIGSERIAL PRIMARY KEY,
    adapter_id          UUID NOT NULL REFERENCES vm_adapters(id) ON DELETE CASCADE,
    resource_id         UUID NOT NULL REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    ip_address          INET NOT NULL,
    family              SMALLINT NOT NULL
);
```

**`vm_adapter_ips`에 `resource_id`를 중복 저장하는 이유**: IP 역조회(FR-404)에서 어댑터 조인 없이
바로 VM에 도달하기 위함이다. 최다 사용 시나리오의 응답 시간이 우선이다.

### 2.5 기타 자원

```sql
CREATE TABLE hosts (
    resource_id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id TEXT NOT NULL, name TEXT NOT NULL,
    fqdn TEXT, management_ip INET,
    connection_state TEXT, in_maintenance BOOLEAN NOT NULL DEFAULT false, boot_time TIMESTAMPTZ,
    vendor TEXT, model TEXT, serial_number TEXT,
    cpu_model TEXT, cpu_sockets INTEGER, cpu_cores INTEGER, cpu_mhz INTEGER,
    memory_bytes BIGINT,
    hypervisor_product TEXT, hypervisor_version TEXT, hypervisor_build TEXT,
    cluster_native_id TEXT,
    lifecycle TEXT NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, missing_since TIMESTAMPTZ,
    UNIQUE (connection_id, native_id)
);

CREATE TABLE clusters (
    resource_id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id TEXT NOT NULL, name TEXT NOT NULL,
    host_count INTEGER NOT NULL DEFAULT 0, vm_count INTEGER NOT NULL DEFAULT 0,
    total_cpu_cores INTEGER, total_memory_bytes BIGINT,
    ha_enabled BOOLEAN, drs_enabled BOOLEAN,
    lifecycle TEXT NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, missing_since TIMESTAMPTZ,
    UNIQUE (connection_id, native_id)
);

CREATE TABLE datastores (
    resource_id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT, capacity_bytes BIGINT, free_bytes BIGINT, provisioned_bytes BIGINT,
    url TEXT, accessible BOOLEAN NOT NULL DEFAULT true,
    lifecycle TEXT NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, missing_since TIMESTAMPTZ,
    UNIQUE (connection_id, native_id)
);

CREATE TABLE networks (
    resource_id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT, vlan_id INTEGER, connected_vm_count INTEGER NOT NULL DEFAULT 0,
    lifecycle TEXT NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, missing_since TIMESTAMPTZ,
    UNIQUE (connection_id, native_id)
);

CREATE TABLE snapshots (
    resource_id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id TEXT NOT NULL, name TEXT NOT NULL,
    vm_native_id TEXT NOT NULL, description TEXT,
    created_at_hv TIMESTAMPTZ, size_bytes BIGINT,
    parent_native_id TEXT, is_current BOOLEAN NOT NULL DEFAULT false,
    lifecycle TEXT NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, missing_since TIMESTAMPTZ,
    UNIQUE (connection_id, native_id)
);
```

### 2.6 메타데이터 — 수집과 분리 (FR-602)

```sql
CREATE TABLE resource_metadata (
    resource_id     UUID PRIMARY KEY,               -- FK 없음: 자원 유형이 여러 테이블에 분산
    resource_type   TEXT NOT NULL,
    owner           TEXT,
    team            TEXT,
    purpose         TEXT,
    environment     TEXT,
    criticality     TEXT,
    service_name    TEXT,
    cost_center     TEXT,
    lifecycle_note  TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    updated_by      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_metadata_owner ON resource_metadata (owner);
CREATE INDEX idx_metadata_env ON resource_metadata (environment);
CREATE INDEX idx_metadata_tags ON resource_metadata USING gin (tags);
```

**별도 테이블이 FR-602의 구조적 보장이다.** 수집 경로의 저장소 구현이 이 테이블에 대한 쓰기 메서드를 갖지 않게 한다.

### 2.7 식별 키 인덱스 (FR-302)

```sql
CREATE TABLE resource_identities (
    resource_id     UUID NOT NULL,
    resource_type   TEXT NOT NULL,
    connection_id   UUID NOT NULL,
    rule            SMALLINT NOT NULL,              -- 1 | 2 | 3
    key_value       TEXT NOT NULL,
    PRIMARY KEY (rule, key_value, resource_id)
);
CREATE INDEX idx_identities_lookup ON resource_identities (rule, key_value);
CREATE INDEX idx_identities_resource ON resource_identities (resource_id);
```

2·3순위 식별을 매 수집마다 전체 스캔하지 않기 위한 인덱스 테이블이다.

### 2.8 수집 이력 (FR-205)

```sql
CREATE TABLE collection_runs (
    run_id          UUID PRIMARY KEY,
    connection_id   UUID NOT NULL REFERENCES connections(connection_id) ON DELETE CASCADE,
    trigger         TEXT NOT NULL,                  -- scheduled | manual
    triggered_by    TEXT,
    status          TEXT NOT NULL,                  -- running | success | partial | failed
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,
    created_count   INTEGER NOT NULL DEFAULT 0,
    updated_count   INTEGER NOT NULL DEFAULT 0,
    missing_count   INTEGER NOT NULL DEFAULT 0,
    change_count    INTEGER NOT NULL DEFAULT 0,
    error_summary   TEXT
);
CREATE INDEX idx_runs_conn_time ON collection_runs (connection_id, started_at DESC);

CREATE TABLE collection_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    resource_type   TEXT NOT NULL,
    collected_count INTEGER NOT NULL DEFAULT 0,
    failed          BOOLEAN NOT NULL DEFAULT false,
    skipped         BOOLEAN NOT NULL DEFAULT false,
    error           TEXT,
    elapsed_ms      INTEGER
);
```

### 2.9 인덱스 (NFR-101·102)

```sql
-- IP 역조회 (FR-404) — 최다 사용 시나리오, 1초 이내 목표
CREATE INDEX idx_adapter_ips_addr ON vm_adapter_ips (ip_address);
CREATE INDEX idx_adapter_ips_resource ON vm_adapter_ips (resource_id);

-- 통합 검색 (FR-403)
CREATE INDEX idx_vm_name_trgm ON virtual_machines USING gin (name gin_trgm_ops);
CREATE INDEX idx_vm_hostname_trgm ON virtual_machines USING gin (guest_hostname gin_trgm_ops);
CREATE INDEX idx_adapters_mac ON vm_adapters (mac_address);

-- 필터 (FR-405) — 활성 자원만 부분 인덱스로
CREATE INDEX idx_vm_conn_active ON virtual_machines (connection_id) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_power_active ON virtual_machines (power_state) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_cluster ON virtual_machines (cluster_native_id) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_host ON virtual_machines (host_native_id) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_os ON virtual_machines (guest_os_name) WHERE lifecycle = 'active';

-- 데이터 품질 (FR-502·504)
CREATE INDEX idx_vm_guest_avail ON virtual_machines (guest_availability) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_last_seen ON virtual_machines (last_seen_at);

-- 리포트 (FR-803)
CREATE INDEX idx_vm_snapshot_age ON virtual_machines (latest_snapshot_at)
    WHERE snapshot_count > 0 AND lifecycle = 'active';

-- 미발견 정리 배치
CREATE INDEX idx_vm_missing_since ON virtual_machines (missing_since) WHERE lifecycle = 'missing';
```

**부분 인덱스(`WHERE lifecycle = 'active'`)를 쓰는 이유**: 조회의 대부분이 활성 자원 대상이므로
인덱스 크기를 줄이고 적중률을 높인다.

---

## 3. Upsert — CI 식별 규칙 적용 (FR-302·303)

### 3.1 알고리즘

```python
async def upsert_virtual_machines(
    self, connection_id: UUID, vms: Sequence[VirtualMachine], observed_at: datetime
) -> UpsertResult:
    created = updated = unchanged = 0
    changes: list[ResourceChange] = []
    duplicates: list[DuplicateCandidate] = []

    for vm in vms:
        keys = build_vm_identity_keys(vm)                       # 계획 02 §7
        match = await self._find_by_identity(connection_id, keys)

        if match is None:
            resource_id = await self._insert_vm(vm, observed_at)
            created += 1
            changes.append(_created_change(resource_id, vm, observed_at))
        else:
            if match.connection_id != connection_id:
                # 다른 연결의 자원과 2·3순위 매칭 — 자동 병합하지 않는다 (D-006)
                duplicates.append(DuplicateCandidate(
                    existing_resource_id=match.resource_id,
                    existing_connection_id=match.connection_id,
                    incoming_connection_id=connection_id,
                    matched_rule=match.rule, matched_value=match.value,
                    detected_at=observed_at,
                ))
                resource_id = await self._insert_vm(vm, observed_at)   # 별도 자원으로 생성
                created += 1
            else:
                previous = await self._load_vm(match.resource_id)
                merged = _merge_guest(vm, previous)                     # §3.3
                diff = diff_virtual_machine(previous, merged)           # 계획 12 §2
                await self._update_vm(match.resource_id, merged, observed_at)
                if diff:
                    changes.append(ResourceChange(
                        resource_id=match.resource_id, detected_at=observed_at,
                        change_type=ChangeType.UPDATED, field_changes=tuple(diff),
                    ))
                    updated += 1
                else:
                    unchanged += 1
        await self._sync_identities(resource_id, keys, connection_id)

    return UpsertResult(created=created, updated=updated, unchanged=unchanged,
                        changes=tuple(changes), duplicate_candidates=tuple(duplicates))
```

### 3.2 식별 조회

```python
async def _find_by_identity(
    self, connection_id: UUID, keys: list[IdentityKey]
) -> IdentityMatch | None:
    """우선순위 순으로 조회한다. 1순위는 같은 연결로 한정한다."""
    for key in sorted(keys, key=lambda k: k.rule):
        row = await self._session.execute(
            select(ResourceIdentityRow.resource_id, ResourceIdentityRow.connection_id)
            .where(ResourceIdentityRow.rule == key.rule,
                   ResourceIdentityRow.key_value == key.value)
            .limit(2)                        # 2건 이상이면 모호 — 다음 순위로
        )
        rows = row.all()
        if len(rows) == 1:
            return IdentityMatch(resource_id=rows[0][0], connection_id=rows[0][1],
                                 rule=key.rule, value=key.value)
        if len(rows) > 1:
            logger.warning("식별 키 모호", extra={"rule": key.rule, "count": len(rows)})
            continue                         # 다음 우선순위로
    return None
```

**`limit(2)`로 모호성을 감지한다.** 같은 BIOS UUID를 가진 클론이 여러 개면 임의로 하나를 고르면 안 된다.

### 3.3 게스트 정보 병합 (FR-501, 계획 12 §2.4)

```python
def _merge_guest(incoming: VirtualMachine, previous: VirtualMachine) -> VirtualMachine:
    """도구가 미동작이면 이전 게스트 값을 유지한다.

    도구가 멈춘 것이지 VM의 IP가 없어진 것이 아니다.
    마지막으로 알려진 IP는 장애 대응에 유용하다.
    """
    return replace(incoming, guest=incoming.guest.with_fallback(previous.guest))
```

### 3.4 메타데이터 보존 (FR-602)

**`upsert`는 `resource_metadata`를 조회조차 하지 않는다.**
저장소 인터페이스를 분리하여 수집 경로에 메타데이터 쓰기 메서드가 노출되지 않게 한다.

```python
class InventoryWriteRepository(Protocol):
    """수집 경로 전용. 메타데이터 쓰기 메서드가 없다."""
    async def upsert_virtual_machines(...) -> UpsertResult: ...
    async def mark_missing(...) -> MissingResult: ...


class MetadataRepository(Protocol):
    """메타데이터 전용. 계획 07이 사용한다."""
    async def upsert_metadata(...) -> ResourceMetadata: ...
```

### 3.5 배치 처리

```python
BATCH_SIZE = 500

async def upsert_batched(self, connection_id, vm_iter, observed_at) -> UpsertResult:
    """배치 단위로 커밋한다. 트랜잭션이 너무 크면 롤백 비용과 락 경합이 커진다."""
    totals = UpsertAccumulator()
    batch: list[VirtualMachine] = []
    async for vm in vm_iter:
        batch.append(vm)
        if len(batch) >= BATCH_SIZE:
            async with self._session.begin():
                totals.add(await self.upsert_virtual_machines(connection_id, batch, observed_at))
            batch.clear()
    if batch:
        async with self._session.begin():
            totals.add(await self.upsert_virtual_machines(connection_id, batch, observed_at))
    return totals.result()
```

---

## 4. 미발견 처리 (FR-307)

```python
async def mark_missing(
    self, connection_id: UUID, resource_type: ResourceType,
    seen_native_ids: set[str], at: datetime,
) -> MissingResult:
    """이번 수집에서 보이지 않은 자원을 missing으로 전환한다.

    수집에 실패한 자원 유형에는 절대 호출하면 안 된다 (§11).
    """
    table = self._table_for(resource_type)
    existing_total = await self._count_active(connection_id, resource_type)

    stmt = (
        update(table)
        .where(table.c.connection_id == connection_id,
               table.c.lifecycle == "active",
               table.c.native_id.notin_(seen_native_ids))
        .values(lifecycle="missing", missing_since=at)
    )
    result = await self._session.execute(stmt)
    return MissingResult(marked=result.rowcount, existing_total=existing_total)
```

### 4.1 재발견 처리

`upsert`에서 자원이 다시 보이면 `lifecycle='active'`, `missing_since=NULL`로 복귀시키고
`RESTORED` 변경 이력을 남긴다.

### 4.2 폐기 전환 배치

```python
async def retire_expired_missing(self, grace: timedelta, now: datetime) -> int:
    """유예 기간이 지난 missing 자원을 retired로 전환한다.

    물리 삭제하지 않는다. 이력·감사 추적을 유지한다.
    """
    cutoff = now - grace
    stmt = (
        update(VirtualMachineRow)
        .where(VirtualMachineRow.lifecycle == "missing",
               VirtualMachineRow.missing_since < cutoff)
        .values(lifecycle="retired")
    )
    return (await self._session.execute(stmt)).rowcount
```

스케줄러에 일 1회 등록한다. `[TODO]` 유예 기간 확정 전까지 기본 7일.

### 4.3 대량 미발견 감지 (FR-111)

```python
MISSING_RATIO_ALERT_THRESHOLD = 0.5

async def apply_missing_with_guard(
    self, connection_id, resource_type, seen_ids, at
) -> MissingResult:
    """미발견 비율이 임계값을 넘으면 경고하고 retired 자동 전환을 보류한다."""
    result = await self.mark_missing(connection_id, resource_type, seen_ids, at)
    if result.missing_ratio >= MISSING_RATIO_ALERT_THRESHOLD and result.existing_total > 0:
        logger.error(
            "대량 미발견 감지 — 연결이 다른 하이퍼바이저를 가리킬 수 있습니다",
            extra={"connection_id": str(connection_id), "ratio": result.missing_ratio,
                   "marked": result.marked, "total": result.existing_total},
        )
        await self._flag_connection_for_review(connection_id, result)
    return result
```

---

## 5. 관계와 조회 전략

### 5.1 논리 참조를 쓰는 이유

VM의 `host_native_id`·`cluster_native_id`는 **FK가 아닌 문자열**이다.

- FK로 두면 Host를 먼저 수집해야 하는 순서 의존이 생긴다
- Host 수집이 실패하면 VM 저장까지 막힌다 (부분 실패 허용 위배 — FR-204)
- 관계 해석은 조회 시점에 조인으로 수행한다 (계획 07 §4)

### 5.2 연결 삭제 (FR-109)

```python
async def disconnect_resources(self, connection_id: UUID) -> dict[ResourceType, int]:
    """연결 삭제 시 자원을 보존하되 disconnected로 전환한다 (권장안).

    메타데이터·변경 이력은 유지된다.
    """
    counts = {}
    for rtype in ResourceType:
        table = self._table_for(rtype)
        stmt = (update(table)
                .where(table.c.connection_id == connection_id,
                       table.c.lifecycle != "disconnected")
                .values(lifecycle="disconnected"))
        counts[rtype] = (await self._session.execute(stmt)).rowcount
    return counts


async def count_impact(self, connection_id: UUID) -> dict[ResourceType, int]:
    """삭제 전 영향 범위를 계산한다 (API 확인 절차용)."""
```

`connections` 테이블의 FK가 `ON DELETE RESTRICT`이므로, 연결 행 자체는
자원을 `disconnected`로 전환한 뒤에 삭제하거나 보존한다. `[TODO]` 정책 확정 시 조정.

---

# Part B. 수집 스케줄러 (Wave 3)

## 6. 구성

```
src/orchestration/
├── scheduler.py     APScheduler — 연결별 주기 등록/해제
├── collector.py     InventoryCollector — 단일 연결 수집 실행
└── maintenance.py   정리 배치 (retired 전환, 이력 보존)
```

**API 서버와 별도 프로세스로 기동한다** (NFR-107). `python -m src.main --mode worker`.

## 7. 수집 실행기 (`collector.py`)

```python
COLLECTION_ORDER = [
    ResourceType.HOST,            # VM의 host 참조 해석에 필요
    ResourceType.CLUSTER,
    ResourceType.VIRTUAL_MACHINE,
    ResourceType.DATASTORE,
    ResourceType.NETWORK,
    ResourceType.SNAPSHOT,
]


class InventoryCollector:
    def __init__(
        self, reader_factory: ReaderFactory, repo: InventoryWriteRepository,
        runs: CollectionRunRepository, history: ChangeHistoryService, settings: Settings,
    ) -> None: ...

    async def collect(self, connection: Connection, trigger: str, actor: str | None) -> RunSummary:
        run = await self._runs.start(connection.connection_id, trigger, actor)
        observed_at = datetime.now(UTC)
        reader = self._reader_factory(connection)
        totals = RunAccumulator()

        try:
            await reader.start_session()
        except AuthenticationError as exc:
            await self._handle_auth_failure(connection, exc, run)      # §8.2
            return await self._runs.finish(run, status="failed", error=str(exc))
        except UnreachableError as exc:
            await self._handle_unreachable(connection, exc, run)
            return await self._runs.finish(run, status="failed", error=str(exc))

        try:
            for rtype in COLLECTION_ORDER:
                if not self._should_collect(connection, reader, rtype):
                    continue
                await self._collect_type(reader, connection, rtype, observed_at, totals, run)
        finally:
            await reader.close_session()

        outcomes = list(reader.get_outcomes())
        await self._runs.record_outcomes(run, outcomes)
        status = self._resolve_status(outcomes)
        await self._on_success(connection)
        return await self._runs.finish(run, status=status, totals=totals)
```

### 7.1 자원 유형별 수집

```python
async def _collect_type(self, reader, connection, rtype, observed_at, totals, run) -> None:
    seen_ids: set[str] = set()
    batch: list[Any] = []
    iterator = self._iterator_for(reader, rtype)

    async for resource in iterator:
        seen_ids.add(resource.native_id)
        batch.append(resource)
        if len(batch) >= BATCH_SIZE:
            result = await self._upsert(connection.connection_id, rtype, batch, observed_at)
            totals.add(result)
            await self._history.record(result.changes, run_id=run.run_id)
            batch.clear()

    if batch:
        result = await self._upsert(connection.connection_id, rtype, batch, observed_at)
        totals.add(result)
        await self._history.record(result.changes, run_id=run.run_id)

    # 이 유형의 수집이 성공했을 때만 미발견 처리 (§11)
    outcome = self._outcome_for(reader, rtype)
    if outcome is not None and not outcome.failed and not outcome.error:
        missing = await self._repo.apply_missing_with_guard(
            connection.connection_id, rtype, seen_ids, observed_at
        )
        totals.add_missing(missing.marked)
    else:
        logger.info("수집 실패로 미발견 처리 생략",
                    extra={"connection_id": str(connection.connection_id),
                           "resource_type": rtype.value})
```

**`outcome.error`가 있으면 건너뛰는 것**이 중요하다. Hyper-V 클러스터에서 일부 노드가 실패하면
`failed=False`이지만 `error`가 채워진다 (계획 05 §9). 이때 미발견 처리하면 그 노드의 VM이 전부 사라진다.

### 7.2 수집 상태 판정

```python
def _resolve_status(self, outcomes: Sequence[CollectionOutcome]) -> str:
    effective = [o for o in outcomes if not o.skipped]
    if not effective:
        return "failed"
    if all(o.failed for o in effective):
        return "failed"
    if any(o.failed or o.error for o in effective):
        return "partial"
    return "success"
```

---

## 8. 실패 처리 (FR-114, CST-05) — 가장 중요한 로직

### 8.1 분류

```python
match error:
    case AuthenticationError():     # retryable = False
        → 재시도 없음
        → connection.status = CREDENTIAL_ERROR
        → 스케줄에서 제외
        → 관리자 알림 (FR-117)
    case PermissionError():
        → 재시도 없음, status = PERMISSION_ERROR
    case UnreachableError():        # retryable = True
        → 최대 3회 재시도 (지수 백오프)
        → 계속 실패 시 status = UNREACHABLE, consecutive_failures 증가
        → 다음 주기에 재시도 (스케줄 유지)
```

### 8.2 인증 실패 처리

```python
async def _handle_auth_failure(self, connection, exc, run) -> None:
    """인증 실패 시 재시도하지 않고 연결을 자격증명 오류로 전환한다.

    AD 통합 서비스 계정에 잘못된 비밀번호로 반복 재시도하면 계정이 잠기고,
    그 계정을 쓰는 다른 시스템까지 연쇄 장애가 발생한다 (CST-05).
    """
    await self._connections.update_status(
        connection.connection_id,
        status=ConnectionStatus.CREDENTIAL_ERROR,
        last_error=str(exc),
        increment_failures=True,
    )
    await self._scheduler.unschedule(connection.connection_id)     # 주기 실행 제거
    await self._notifier.notify_credential_error(connection)       # FR-117
    logger.error("인증 실패 — 수집 중단 및 스케줄 해제",
                 extra={"connection_id": str(connection.connection_id)})
```

**`unschedule`이 핵심이다.** 상태만 바꾸고 스케줄을 남겨두면 다음 주기에 또 시도하여 계정이 잠긴다.

### 8.3 재개

관리자가 자격증명을 갱신하면(계획 08 FR-108) 연결 상태를 `ACTIVE`로 되돌리고 스케줄을 재등록한다.
**연결 테스트 성공을 확인한 뒤에만 재등록한다.**

### 8.4 데이터 보존 (NFR-302)

**어떤 실패에서도 기존 수집 데이터를 삭제하지 않는다.** 신선도(`last_seen_at`)만 오래된 채로 남고,
UI가 이를 경고로 표시한다 (계획 11 §3.3).

---

## 9. 스케줄링 (`scheduler.py`)

```python
class CollectionScheduler:
    def __init__(self, collector: InventoryCollector, settings: Settings) -> None:
        self._sched = AsyncIOScheduler(timezone="UTC")
        self._sem = asyncio.Semaphore(settings.collection_max_concurrent_connections)

    async def schedule(self, connection: Connection) -> None:
        if not connection.is_collectable:
            return                                        # 비활성·자격증명 오류는 등록하지 않음
        self._sched.add_job(
            self._run, trigger=IntervalTrigger(minutes=connection.collection_interval_minutes),
            id=str(connection.connection_id), replace_existing=True,
            args=[connection.connection_id, "scheduled", None],
            max_instances=1,                              # 동일 연결 중복 실행 방지
            coalesce=True,                                # 밀린 실행을 합침
            misfire_grace_time=300,
        )

    async def unschedule(self, connection_id: UUID) -> None:
        self._sched.remove_job(str(connection_id), jobstore="default")

    async def _run(self, connection_id: UUID, trigger: str, actor: str | None) -> None:
        async with self._sem:                             # 동시 수집 연결 수 제한
            if not await self._acquire_lock(connection_id):
                logger.info("이미 수집 중 — 건너뜀", extra={"connection_id": str(connection_id)})
                return
            try:
                connection = await self._connections.get(connection_id)
                await self._collector.collect(connection, trigger, actor)
            finally:
                await self._release_lock(connection_id)
```

### 9.1 분산 락 (Redis)

여러 워커 프로세스가 뜰 수 있으므로 `max_instances=1`만으로는 부족하다.

```python
LOCK_KEY = "collection:lock:{connection_id}"

async def _acquire_lock(self, connection_id: UUID) -> bool:
    ttl = self._settings.collection_timeout_seconds * 2 + 600
    return bool(await self._redis.set(
        LOCK_KEY.format(connection_id=connection_id), self._worker_id,
        nx=True, ex=ttl,
    ))
```

TTL을 넉넉히 잡아 워커가 죽어도 락이 영구히 남지 않게 한다.

### 9.2 수동 수집 (FR-202)

```python
async def trigger_now(self, connection_id: UUID, actor: str) -> UUID:
    connection = await self._connections.get(connection_id)
    if connection.status is ConnectionStatus.CREDENTIAL_ERROR:
        raise ValidationError(
            "자격증명 오류 상태입니다. 자격증명을 갱신한 뒤 다시 시도하세요."
        )                                                 # 계정 잠금 방지 (계획 08 §9)
    if await self._is_running(connection_id):
        raise DuplicateError("이미 수집이 진행 중입니다.")
    return await self._enqueue(connection_id, "manual", actor)
```

## 10. 정리 배치 (`maintenance.py`)

| 작업 | 주기 | 내용 |
|---|---|---|
| `retire_expired_missing` | 일 1회 | 유예 경과 missing → retired (§4.2) |
| `purge_old_history` | 일 1회 | 보존 기간 경과 변경 이력 삭제 (계획 12 §5.1) |
| `purge_old_runs` | 일 1회 | 오래된 수집 이력 정리 |
| `refresh_quality_metrics` | 5분 | 대시보드 집계 캐시 갱신 (계획 12 §6.3) |

## 11. 구현 순서

**Wave 1 (저장소)**

| # | 작업 | 검증 |
|---|---|---|
| 1 | Alembic 초기 마이그레이션 + `pg_trgm` | `alembic upgrade head` 성공, 다운그레이드 확인 |
| 2 | SQLAlchemy 모델 ↔ 도메인 매핑 | 왕복 변환 테스트 (모든 필드 보존) |
| 3 | `_find_by_identity` | 순위별 조회, 모호 시 다음 순위, 교차 연결 감지 |
| 4 | `upsert_virtual_machines` | **2회 수집 → 레코드 1건**, 변경분만 changes |
| 5 | `_merge_guest` | 도구 미동작 시 이전 IP·OS 유지 |
| 6 | 하위 컬렉션 동기화 | 디스크·어댑터·IP 삭제/추가 반영 |
| 7 | `mark_missing` | 미발견 전환, 재등장 시 active 복귀 |
| 8 | `apply_missing_with_guard` | 50% 초과 시 경고 + 플래그 |
| 9 | `disconnect_resources`·`count_impact` | 메타데이터·이력 보존 |
| 10 | 인덱스 | IP 역조회 `EXPLAIN`에 Index Scan 확인 |

**Wave 3 (스케줄러)**

| # | 작업 | 검증 |
|---|---|---|
| 11 | `InventoryCollector` (목 커넥터) | 전체 흐름 통합 테스트 |
| 12 | 실패 분류 | **인증 실패 시 재시도 0회, unschedule 호출** |
| 13 | 부분 실패 | VM 성공 + Datastore 실패 → VM만 반영, **미발견 처리는 VM만** |
| 14 | 노드 부분 실패 | `outcome.error` 있으면 미발견 처리 생략 |
| 15 | `AsyncIOScheduler` | 등록·해제, `max_instances=1` |
| 16 | Redis 분산 락 | 동시 실행 차단, TTL 만료 |
| 17 | 수동 수집 | 자격증명 오류 시 거부, 중복 실행 거부 |
| 18 | 정리 배치 | 유예 경과 전환, 이력 정리 |

## 12. 완료 기준

- [ ] 동일 자원 2회 수집 → 레코드 1건 (FR-303)
- [ ] `UNIQUE (connection_id, native_id)` 제약이 DB에 존재
- [ ] 메타데이터 입력 후 재수집 → 값 보존 (FR-602)
- [ ] 도구 미동작 전환 시 이전 게스트 값 유지 (FR-501)
- [ ] 자원 소실 → `missing` 유예, 즉시 삭제 안 됨 (FR-307)
- [ ] 재등장 시 `active` 복귀 + `RESTORED` 이력
- [ ] 호스트 변경(vMotion) → 동일 `resource_id` 유지 + 이력 (FR-305)
- [ ] 미발견 50% 초과 시 경고 + retired 자동 전환 보류 (FR-111)
- [ ] 연결 A 실패 + B 성공 → B 정상, A 기존 데이터 유지 (FR-204, NFR-301)
- [ ] **인증 실패 시 재시도 0회 + 스케줄 해제 + 상태 전환** (FR-114)
- [ ] **부분 실패 시 실패 유형에 `mark_missing` 미호출**
- [ ] 자격증명 오류 연결의 수동 수집이 거부됨
- [ ] IP 역조회가 Index Scan (5,000건 기준 1초 이내)
- [ ] `arch_check.py` 통과 — orchestration이 어댑터를 직접 import하지 않음

## 13. 주의사항

- **`mark_missing` 오용이 이 프로젝트에서 가장 위험한 버그다.** 수집 실패를 "자원 없음"으로 오해하면 전체 인벤토리가 미발견 처리된다. 조건을 세 겹으로 확인한다: 유형별 outcome이 `failed=False`, `error=None`, 그리고 연결 자체가 성공.
- `orchestration`은 `src.infrastructure.vcenter|hyperv`를 import할 수 없다. 팩토리를 주입받는다 (계획 03 §7).
- SQLAlchemy 모델을 도메인 엔티티로 겸용하지 않는다. 매핑 코드가 늘더라도 계층을 지킨다.
- 배치 트랜잭션을 너무 크게 잡으면 롤백 비용과 락 경합이 커진다 (§3.5).
- 인증 실패 시 `unschedule`을 빠뜨리면 상태만 바뀌고 다음 주기에 또 시도한다 (§8.2).
- `[TODO]` 확정 대기: 수집 주기(NFR-105), 유예 기간(FR-307), 연결 삭제 정책(FR-109), 관리 규모(NFR-104 — 확정 시 배치 크기·인덱스 재검토)
