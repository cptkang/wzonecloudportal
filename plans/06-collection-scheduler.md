# 06. 저장소 스키마 및 수집 스케줄러

> Wave: 1 (저장소) · 3 (스케줄러)
> 계층: infrastructure (`src/infrastructure/db/`, `repository/`) · orchestration (`src/orchestration/`)
> 담당 요건: FR-2xx 전체, FR-303·305·307·308, FR-109·111·113·114, NFR-101·107·301~305
> 의존: 02, 03, 10
> 관련 결정: D-006, D-007, D-008

## 1. 목적

수집한 인벤토리를 저장하고, 연결별로 주기 수집을 실행한다.
**CI 식별 규칙(FR-302)을 실제로 강제하는 지점**이며, 중복 생성·메타데이터 소실·부분 실패 처리의 성패가 여기서 갈린다.

이 계획은 두 Wave로 나뉜다.
- **Wave 1**: DB 스키마 + 저장소 구현 (다른 모듈이 의존)
- **Wave 3**: 스케줄러 + 수집 실행기 (어댑터 완성 후)

---

# Part A. 저장소 (Wave 1)

## 2. DB 스키마

### 2.1 테이블 목록

| 테이블 | 용도 | 비고 |
|---|---|---|
| `connections` | 하이퍼바이저 연결 (§2.7) | 자격증명은 암호문 |
| `virtual_machines` | VM 인벤토리 | 핵심 테이블 |
| `vm_disks`, `vm_adapters` | VM 하위 컬렉션 | VM 삭제 시 CASCADE |
| `hosts`, `clusters`, `datastores`, `networks`, `snapshots` | 기타 자원 | |
| `resource_metadata` | 포탈 부여 메타데이터 | **수집 테이블과 분리** (FR-602) |
| `resource_identities` | 식별 키 인덱스 | FR-302 조회 최적화 |
| `collection_runs` | 수집 이력 (FR-205) | |
| `collection_outcomes` | 수집 유형별 결과 | 부분 실패 기록 |
| `resource_changes` | 변경 이력 (계획 12) | |
| `audit_events` | 감사 로그 (계획 10) | |
| `users`, `roles`, `user_scopes` | 인증·권한 (계획 09) | |

### 2.2 `virtual_machines` 핵심 컬럼

```sql
resource_id        UUID PRIMARY KEY
connection_id      UUID NOT NULL REFERENCES connections(id)
native_id          TEXT NOT NULL
bios_uuid          TEXT
name               TEXT NOT NULL
power_state        TEXT NOT NULL
-- 스펙
vcpu_count         INT
socket_count       INT
cores_per_socket   INT
memory_mb          BIGINT
hw_version         TEXT
firmware           TEXT
-- 게스트 (도구 의존 — FR-501)
guest_availability TEXT NOT NULL          -- available | tools_not_installed | ...
guest_os_name      TEXT
guest_os_source    TEXT                   -- guest_tools | vm_config (FR-304)
guest_hostname     TEXT
guest_tool_version TEXT
-- 소속 (FR-306)
host_native_id     TEXT
cluster_native_id  TEXT
-- 집계 (조회 성능 — 원본은 vm_disks)
total_provisioned_bytes BIGINT
total_used_bytes        BIGINT
snapshot_count          INT
latest_snapshot_at      TIMESTAMPTZ
-- 수명주기
lifecycle          TEXT NOT NULL          -- active | missing | retired | disconnected
first_seen_at      TIMESTAMPTZ NOT NULL
last_seen_at       TIMESTAMPTZ NOT NULL   -- 신선도 (FR-502)
missing_since      TIMESTAMPTZ            -- 유예 시작 (FR-307)

UNIQUE (connection_id, native_id)         -- CI 식별 1순위를 DB 제약으로 강제
```

**IP 주소는 `vm_adapters`에 정규화 저장한다.** IP 역조회(FR-404)가 최다 사용 시나리오이므로 배열 컬럼이 아닌 별도 행으로 두고 인덱스를 건다.

```sql
CREATE TABLE vm_adapters (
    id UUID PRIMARY KEY,
    resource_id UUID NOT NULL REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    mac_address TEXT,
    adapter_type TEXT,
    network_name TEXT,
    connected BOOLEAN
);
CREATE TABLE vm_adapter_ips (
    adapter_id UUID NOT NULL REFERENCES vm_adapters(id) ON DELETE CASCADE,
    ip_address INET NOT NULL,
    family SMALLINT NOT NULL            -- 4 | 6
);
```

### 2.3 인덱스 (NFR-101·102)

```sql
-- IP 역조회 (FR-404) — 1초 이내 목표
CREATE INDEX idx_adapter_ips_addr ON vm_adapter_ips (ip_address);
-- 통합 검색 (FR-403)
CREATE INDEX idx_vm_name_trgm ON virtual_machines USING gin (name gin_trgm_ops);
CREATE INDEX idx_vm_hostname_trgm ON virtual_machines USING gin (guest_hostname gin_trgm_ops);
CREATE INDEX idx_adapters_mac ON vm_adapters (mac_address);
-- 필터 (FR-405)
CREATE INDEX idx_vm_conn_lifecycle ON virtual_machines (connection_id, lifecycle);
CREATE INDEX idx_vm_power ON virtual_machines (power_state) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_cluster ON virtual_machines (cluster_native_id);
-- 데이터 품질 (FR-504)
CREATE INDEX idx_vm_guest_avail ON virtual_machines (guest_availability);
```

`pg_trgm` 확장이 필요하다. 마이그레이션 첫 단계에서 `CREATE EXTENSION IF NOT EXISTS pg_trgm`.

### 2.4 `resource_identities` — 식별 키 인덱스

2·3순위 식별(BIOS UUID, MAC+이름)을 매 수집마다 전체 스캔하지 않도록 별도 테이블로 관리한다.

```sql
CREATE TABLE resource_identities (
    resource_id UUID NOT NULL,
    rule SMALLINT NOT NULL,       -- 1 | 2 | 3
    key_value TEXT NOT NULL,
    PRIMARY KEY (rule, key_value, resource_id)
);
CREATE INDEX idx_identities_lookup ON resource_identities (rule, key_value);
```

## 3. 저장소 구현 (`InventoryRepository`)

### 3.1 Upsert — CI 식별 규칙 적용 (FR-302·303)

```
1. build_identity_keys(vm)로 1~3순위 키 생성 (계획 02)
2. 순위 순으로 resource_identities 조회
   - 1순위 매칭 → 해당 resource_id 갱신
   - 2·3순위 매칭 →
       · 같은 connection_id면 갱신 (native_id가 바뀐 경우)
       · 다른 connection_id면 갱신하지 않고 중복 후보로 기록 (FR-308)
   - 미매칭 → 신규 생성
3. resource_identities 갱신 (기존 키 삭제 후 재삽입)
4. last_seen_at 갱신, lifecycle을 active로 복귀
```

**교차 연결 매칭을 자동 병합하지 않는 이유**: 같은 VM이 두 vCenter에 보이는 상황(ELM, 복제)에서 자동 병합하면
어느 연결이 권위 있는지 알 수 없고, 되돌리기도 어렵다. 경고만 남기고 관리자가 판단하게 한다 (D-006).

```python
@dataclass
class UpsertResult:
    created: int
    updated: int
    duplicate_candidates: list[DuplicateCandidate]   # FR-308
```

### 3.2 메타데이터 보존 (FR-602)

**upsert는 `resource_metadata` 테이블을 절대 건드리지 않는다.**
테이블 분리 자체가 구조적 보장이며, 코드에서 추가로 지킬 규칙은 "수집 경로에서 메타데이터 저장소를 주입받지 않는 것"이다.

### 3.3 미발견 처리 (FR-307)

```python
async def mark_missing(connection_id: UUID, seen_native_ids: set[str], at: datetime) -> int:
    """이번 수집에서 보이지 않은 자원을 missing으로 전환한다."""
```

- `lifecycle = 'active'` 이고 `native_id NOT IN seen_ids` → `missing`, `missing_since = at`
- 다음 수집에서 다시 보이면 `active`로 복귀하고 `missing_since = NULL`
- `missing_since + grace_days` 경과 시 별도 정리 작업이 `retired`로 전환
- **`retired`도 물리 삭제하지 않는다.** 이력·감사 추적 유지

> **부분 실패 시 절대 호출하지 않는다.** VM 조회가 실패했는데 `mark_missing`을 부르면
> 그 연결의 전체 VM이 미발견 처리된다. 수집 성공한 자원 유형에 대해서만 호출한다 (§7.2).

### 3.4 연결 대상 변경 감지 (FR-111)

한 수집에서 **기존 자원의 대부분이 미발견**이면 연결이 다른 하이퍼바이저를 가리키게 된 신호다.

```python
MISSING_RATIO_ALERT_THRESHOLD = 0.5    # 50% 이상 미발견 시

if existing_count > 0 and missing_count / existing_count >= THRESHOLD:
    → mark_missing을 적용하되 경고 이벤트 기록, 관리자 알림
    → retired 자동 전환은 보류 (관리자 확인 전까지)
```

### 3.5 연결 삭제 시 처리 (FR-109)

`[TODO]` 정책 확정 전 **권장안(보존)** 으로 구현한다.

```python
async def disconnect_resources(connection_id: UUID) -> int:
    """연결 삭제 시 자원을 보존하되 disconnected 상태로 전환한다."""
```

- `lifecycle = 'disconnected'`, 조회 시 기본 필터에서 제외(명시 요청 시 표시)
- 메타데이터·변경 이력은 유지
- 삭제 전 영향 건수를 반환하여 API가 확인 절차에 사용 (FR-109)

---

# Part B. 수집 스케줄러 (Wave 3)

## 4. 구성

```
src/orchestration/
├── scheduler.py     APScheduler — 연결별 주기 등록/해제
└── collector.py     InventoryCollector — 단일 연결 수집 실행
```

**API 서버와 별도 프로세스로 기동한다** (NFR-107). `src/main.py`에 `--mode api|worker` 분기.

## 5. 수집 실행 흐름 (단일 연결)

```
1. 연결 조회 → 비활성/자격증명 오류면 skip
2. collection_runs 레코드 생성 (status=running)
3. reader_factory(connection)로 어댑터 생성, start_session()
4. 자원 유형별 수집 (VM → Host → Cluster → Datastore → Network → Snapshot)
   - AsyncIterator를 BATCH_SIZE(기본 500)씩 모아 upsert
   - 유형별 성공/실패를 collection_outcomes에 기록
   - 한 유형 실패가 다른 유형을 중단시키지 않음 (FR-204)
5. 성공한 유형에 대해서만 mark_missing (§3.3)
6. 변경 이력 생성 위임 (계획 12)
7. close_session(), collection_runs 완료 기록
```

### 5.1 순서가 중요한 이유

VM의 `host_native_id`·`cluster_native_id`가 참조 무결성을 갖도록 하려면 Host·Cluster를 먼저 수집하는 편이 자연스러우나,
**VM이 가장 중요한 자원이므로 먼저 수집한다.** 참조는 FK가 아닌 논리 참조(native_id 문자열)로 두어
수집 순서에 의존하지 않게 한다. 관계 해석은 조회 시점에 수행한다 (계획 07).

## 6. 스케줄링 (FR-201·202)

- APScheduler `AsyncIOScheduler`, 연결별 `IntervalTrigger`
- 연결 등록·수정·삭제 시 스케줄 갱신 (계획 08의 유스케이스가 스케줄러에 통지)
- **수동 수집(FR-202)** 은 스케줄과 별개로 즉시 실행. 이미 실행 중이면 거부하고 진행 상태 반환
- 동일 연결의 동시 수집 방지: Redis 분산 락(`collection:lock:{connection_id}`, TTL = 타임아웃 × 2)

## 7. 부하 제어와 실패 처리

### 7.1 부하 제어 (FR-206, NFR-304)

| 항목 | 기본값 | 설정 키 |
|---|---|---|
| 호출 타임아웃 | 60초 | `collection_timeout_seconds` |
| 재시도 | 최대 3회 (지수 백오프) | `collection_max_retries` |
| 동시 수집 연결 수 | 4 | `collection_max_concurrent_connections` |
| 배치 크기 | 500 | — |

동시 실행 제한은 `asyncio.Semaphore`로 구현한다.

### 7.2 실패 분류 (FR-114, CST-05) — 가장 중요한 처리

```python
match error:
    case AuthenticationError():        # retryable = False
        → 재시도 없음
        → connection.status = CREDENTIAL_ERROR
        → 스케줄에서 제외 (관리자가 자격증명 갱신 후 재개)
        → 관리자 알림 (FR-117)
    case PermissionError():
        → 재시도 없음, status = PERMISSION_ERROR
    case UnreachableError():           # retryable = True
        → 최대 3회 재시도
        → 계속 실패 시 status = UNREACHABLE, 연속 실패 횟수 증가
        → 다음 주기에 재시도 (스케줄 유지)
```

**어떤 경우에도 기존 수집 데이터를 삭제하지 않는다** (NFR-302). 신선도(`last_seen_at`)만 오래된 채로 남는다.

### 7.3 수집 이력 (FR-205)

`collection_runs`: `run_id`, `connection_id`, `started_at`, `finished_at`, `status`, `duration_ms`,
`created_count`, `updated_count`, `missing_count`, `error_summary`

`collection_outcomes`: `run_id`, `resource_type`, `collected_count`, `failed`, `error`

모든 수집 로그에 `run_id`를 포함한다 (계획 01 §6).

## 8. 증분 갱신 (FR-207, Should)

Phase 1에서는 **전량 수집**으로 구현한다. 증분은 다음 조건이 갖춰진 뒤 도입한다.

- vCenter: `PropertyCollector`의 `WaitForUpdatesEx` 기반 변경 추적 — `docs/00_research_notes.md` §11-2 검증 필요
- Hyper-V: 변경 추적 메커니즘 확인 필요

`ReaderCapabilities.supports_incremental`로 어댑터별 지원 여부를 노출해 두고, 미지원이면 전량 수집으로 폴백한다.

## 9. 구현 순서

**Wave 1 (저장소)**
1. Alembic 초기 마이그레이션 + `pg_trgm` → 검증: `alembic upgrade head` 성공
2. SQLAlchemy 모델 ↔ 도메인 엔티티 매핑 → 검증: 왕복 변환 테스트
3. `upsert_virtual_machines` + 식별 규칙 → 검증: **2회 수집 시 레코드 1건**
4. `mark_missing` → 검증: 자원 소실 시 missing 전환, 재등장 시 active 복귀
5. `disconnect_resources` → 검증: 메타데이터·이력 보존 확인
6. 인덱스 → 검증: IP 역조회 `EXPLAIN`에 인덱스 스캔 확인

**Wave 3 (스케줄러)**
7. `InventoryCollector` (목 커넥터 사용) → 검증: 전체 흐름 통합 테스트
8. 실패 분류 → 검증: **인증 실패 시 재시도 0회** (가장 중요)
9. 부분 실패 → 검증: VM 성공 + Datastore 실패 시 VM만 반영, mark_missing은 VM만
10. `AsyncIOScheduler` 연동 → 검증: 주기 등록·해제, 동시 실행 차단

## 10. 완료 기준

- [ ] 동일 자원 2회 수집 → 레코드 1건 (FR-303)
- [ ] 메타데이터 입력 후 재수집 → 값 보존 (FR-602)
- [ ] 자원 소실 → `missing` 유예, 즉시 삭제되지 않음 (FR-307)
- [ ] 호스트 변경(vMotion) → 동일 `resource_id` 유지 (FR-305)
- [ ] 연결 A 실패 + 연결 B 성공 → B 정상, A 기존 데이터 유지 (FR-204, NFR-301)
- [ ] **인증 실패 시 재시도 0회, 연결이 자격증명 오류 상태로 전환** (FR-114)
- [ ] 부분 실패 시 실패한 유형에 대해 `mark_missing`이 호출되지 않음
- [ ] IP 역조회가 인덱스를 사용 (5,000건 기준 1초 이내)
- [ ] `arch_check.py` 통과 — orchestration이 어댑터를 직접 import하지 않음

## 11. 주의사항

- **`mark_missing`의 오용이 이 프로젝트에서 가장 위험한 버그다.** 수집 실패를 "자원 없음"으로 오해하면 전체 인벤토리가 미발견 처리된다. 성공한 유형에만, 성공한 연결에만 적용한다.
- `orchestration`은 `src.infrastructure.vcenter|hyperv`를 import할 수 없다. 팩토리를 주입받는다 (계획 03 §6).
- SQLAlchemy 모델을 도메인 엔티티로 겸용하지 않는다. 매핑 코드가 늘더라도 계층을 지킨다.
- 배치 upsert 시 트랜잭션을 너무 크게 잡으면 롤백 비용과 락 경합이 커진다. 배치 단위로 커밋한다.
- `[TODO]` 확정 대기: 수집 주기 기본값(NFR-105), 유예 기간(FR-307), 연결 삭제 정책(FR-109), 관리 규모(NFR-104 — 배치 크기·인덱스 재검토 필요)
