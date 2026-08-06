# 12. 변경 이력·수명주기·데이터 품질

> Wave: 3
> 계층: application (`src/application/change_history.py`) · domain (`src/domain/history.py`)
> 담당 요건: FR-7xx(변경 이력), FR-501~505(데이터 품질), FR-307·308
> 의존: 02, 06
> 관련 결정: D-006

## 1. 목적

수집 결과를 비교하여 자원 속성 변경을 감지·기록하고, 신규·삭제 자원을 추적하며, 데이터 품질을 판정한다.

`docs/00_research_notes.md` §9: 변경 추적은 **무엇이 언제 어떻게 바뀌었는지**를 남겨 감사 추적과 문제 해결을 지원한다.

## 2. 변경 감지 (FR-701) — 이 계획의 핵심

### 2.1 감지 방식

수집 시점에 **기존 레코드와 새 값을 필드 단위로 비교**한다. 저장 후 비교하면 이전 값을 잃는다.

```
저장소 upsert 시점:
  1. 기존 레코드 조회 (CI 식별 규칙으로 매칭 — 계획 06 §3.1)
  2. 추적 대상 필드(§2.2)를 비교
  3. 차이가 있으면 ResourceChange 레코드 생성
  4. 자원 레코드 갱신
```

**저장소가 변경 목록을 반환하고, 이 계층이 이력으로 기록한다.** 저장소가 이력까지 쓰면 책임이 섞인다.

```python
@dataclass(frozen=True)
class FieldChange:
    field: str
    old_value: str | None
    new_value: str | None

@dataclass(frozen=True)
class ResourceChange:
    change_id: UUID
    resource_id: UUID
    detected_at: datetime
    run_id: UUID                     # 어느 수집에서 감지되었는지
    change_type: ChangeType          # CREATED | UPDATED | MISSING | RESTORED | RETIRED | METADATA
    field_changes: tuple[FieldChange, ...]
    actor: str | None                # 메타데이터 변경 시 사용자, 수집이면 None
```

### 2.2 추적 대상 필드 (FR-702)

**최소 요건** (spec.md FR-702):

| 분류 | 필드 |
|---|---|
| 네트워크 | IP 주소 목록, MAC 주소 |
| 식별 | 이름, 게스트 호스트명 |
| 스펙 | vCPU 수, 메모리, 디스크 총 용량 |
| 상태 | 전원 상태 |
| 소속 | 호스트, 클러스터 |
| OS | 게스트 OS 이름 |

**추적하지 않는 것** — 노이즈가 되어 이력을 무용지물로 만든다:
- `last_seen_at` (매 수집마다 바뀜)
- 가동 시간, 디스크 실제 사용량 등 연속 변동값
- 스냅샷 용량

> **전원 상태 주의**: 개발·테스트 VM은 매일 켜고 꺼진다. 전원 상태 변경이 이력의 대부분을 차지할 수 있다.
> 이력 조회에서 **변경 유형별 필터**를 제공하고, UI 기본값에서 전원 상태 변경을 제외할지 검토한다.

### 2.3 값 비교 규칙

- IP 목록·MAC 목록은 **정렬 후 비교**한다. 순서 변화는 변경이 아니다
- 부동소수·용량은 임계값 이하 차이를 무시 (디스크 용량은 바이트 단위 반올림 오차 가능)
- **`GuestInfo`가 수집 불가로 전환된 경우**, IP·OS를 "값이 사라짐"으로 기록하지 않는다.
  도구가 멈춘 것이지 VM의 IP가 없어진 게 아니다. `guest_availability` 변경으로만 기록한다 (§2.4)

### 2.4 수집 불가 전환의 처리 — 흔한 오탐

```
이전: guest_availability=AVAILABLE, ip=[10.0.0.5], os="Ubuntu 22.04"
현재: guest_availability=TOOLS_NOT_RUNNING, ip=[], os=None

잘못된 기록:  ip: 10.0.0.5 → (없음),  os: Ubuntu 22.04 → (없음)     ← 오탐
올바른 기록:  guest_availability: available → tools_not_running     ← 이것만
```

**도구 미동작 시 이전 게스트 값을 유지한다.** 마지막으로 알려진 IP는 장애 대응에 유용한 정보이며,
`GuestInfo.availability`와 `last_seen_at`으로 신뢰도를 판단할 수 있다.

## 3. 수명주기 추적 (FR-703·704)

계획 06 §3.3의 `lifecycle` 전이를 이력으로 남긴다.

```
(없음) ──────► ACTIVE      CREATED   신규 발견 (FR-703)
ACTIVE ──────► MISSING     MISSING   수집 결과에서 사라짐 (FR-704)
MISSING ─────► ACTIVE      RESTORED  다시 발견됨
MISSING ─────► RETIRED     RETIRED   유예 경과
ACTIVE ──────► DISCONNECTED          연결 삭제 (FR-109)
```

```python
async def list_new_resources(scope, since: datetime, page) -> PagedResult[...]: ...      # FR-703
async def list_missing_resources(scope, since: datetime, page) -> PagedResult[...]: ...  # FR-704
```

**대량 미발견 경고 (FR-111)**: 한 수집에서 미발견 비율이 임계값(기본 50%)을 넘으면
연결이 다른 하이퍼바이저를 가리키게 된 신호다. 경고 이벤트를 남기고 `RETIRED` 자동 전환을 보류한다 (계획 06 §3.4).

## 4. 자원 이력 타임라인 (FR-705)

```python
async def get_timeline(
    scope, resource_id: UUID, since: datetime | None, change_types: frozenset[ChangeType] | None, page: Page
) -> PagedResult[ResourceChange]: ...
```

- 시간 역순 정렬
- 수집 변경과 메타데이터 변경을 함께 표시하되 `actor`로 구분 (사람 vs 시스템)
- 페이징 필수. 장수명 VM은 이력이 수천 건 쌓인다

## 5. DB 스키마

```sql
CREATE TABLE resource_changes (
    change_id     UUID PRIMARY KEY,
    resource_id   UUID NOT NULL,
    resource_type TEXT NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL,
    run_id        UUID,
    change_type   TEXT NOT NULL,
    actor         TEXT,
    field_changes JSONB NOT NULL          -- [{field, old_value, new_value}, ...]
);
CREATE INDEX idx_changes_resource_time ON resource_changes (resource_id, detected_at DESC);
CREATE INDEX idx_changes_time_type ON resource_changes (detected_at DESC, change_type);
```

`field_changes`를 JSONB로 두는 이유: 필드별 행으로 정규화하면 행 수가 폭증하고, 한 시점의 변경을 묶어 보기 어렵다.
필드 단위 검색이 필요해지면 GIN 인덱스를 추가한다.

### 5.1 보존 기간 (FR-706) — `[TODO]`

미확정. 임시 기본값 **1년**(`history_retention_days=365`).

- 보존 기간 경과 이력을 삭제하는 정리 작업을 스케줄러에 등록
- **삭제 전 아카이브 여부**는 정책 확정 시 결정. 그 전까지는 삭제만 수행하되, 정리 작업 실행 결과를 로그로 남긴다
- 감사 로그(계획 10)와는 보존 정책이 다를 수 있다. 별도 설정 키로 분리

## 6. 데이터 품질 판정 (FR-501~505)

### 6.1 품질 상태

```python
@dataclass(frozen=True)
class DataQuality:
    guest_info_available: bool          # FR-501
    is_stale: bool                      # FR-502 — last_seen_at 기준 초과
    has_required_metadata: bool         # FR-503 — 소유자·환경 존재
    tool_status: GuestInfoAvailability  # FR-504
```

### 6.2 신선도 판정 (FR-502)

```
경고 임계: data_freshness_warning_hours (기본 12시간)
판정: now - last_seen_at > threshold  →  stale
```

**연결이 비활성이거나 수집 실패 중이면** 자원 개별 문제가 아니라 연결 문제다.
UI가 원인을 구분해 표시할 수 있도록 연결 상태를 함께 반환한다.

### 6.3 품질 지표 (FR-505)

대시보드용 집계. 범위 필터를 반영해야 한다 (계획 09 §9).

```python
@dataclass(frozen=True)
class QualityMetrics:
    total: int
    guest_info_available: int       # 비율로 표시
    metadata_complete: int
    stale: int
    by_tool_status: dict[GuestInfoAvailability, int]
```

Redis 캐시 TTL 5분. **범위별로 캐시 키를 분리**한다.

## 7. 중복 후보 관리 (FR-308)

계획 06 §3.1이 감지한 중복 후보를 조회·해소하는 인터페이스를 제공한다.

```python
async def list_duplicate_candidates(scope, page) -> PagedResult[DuplicateCandidate]: ...
async def dismiss_duplicate(scope, actor, candidate_id: UUID, reason: str) -> None: ...
```

**자동 병합하지 않는다** (D-006). 관리자가 확인하고 무시하거나, 연결 설정을 조정한다.

## 8. 구현 순서

1. `ResourceChange`·`FieldChange` 도메인 모델 + 스키마 → 검증: 직렬화
2. 필드 비교 로직 → 검증: **IP 순서 변경이 변경으로 기록되지 않음**
3. **수집 불가 전환 처리** → 검증: 도구 정지 시 IP·OS 소실로 기록되지 않고 availability 변경만 기록
4. 저장소 upsert 연동 → 검증: 2회 수집 중 변경분만 이력 생성
5. 수명주기 전이 이력 → 검증: 5가지 전이 모두 기록
6. 타임라인 조회 → 검증: 역순 정렬, 필터, 페이징
7. 품질 판정 → 검증: 신선도·메타데이터 누락·도구 상태
8. 보존 정리 작업 → 검증: 기간 경과 이력 삭제, 로그 기록
9. 중복 후보 조회

## 9. 완료 기준

- [ ] FR-702의 최소 추적 필드가 모두 감지됨
- [ ] IP·MAC 목록의 순서 변화가 변경으로 기록되지 않음
- [ ] **도구 미동작 전환 시 IP·OS 소실로 오탐되지 않음**
- [ ] 도구 미동작 시 마지막으로 알려진 게스트 값이 유지됨
- [ ] `last_seen_at` 등 노이즈 필드가 이력에 없음
- [ ] 수명주기 5가지 전이가 모두 이력에 남음
- [ ] 타임라인이 수집 변경과 사용자 변경을 `actor`로 구분
- [ ] 품질 집계에 조회 범위가 반영됨
- [ ] 보존 기간 정리 작업이 동작

## 10. 주의사항

- **§2.4의 오탐이 이 계획에서 가장 흔한 결함이다.** VMware Tools가 잠깐 멈추면 전체 VM에 "IP 사라짐" 이력이 대량 생성되어 이력 자체가 신뢰를 잃는다.
- 전원 상태 변경이 이력을 뒤덮지 않도록 필터를 제공한다 (§2.2).
- 이력 테이블은 가장 빠르게 커지는 테이블이다. 인덱스와 보존 정책을 처음부터 넣는다.
- 변경 감지를 저장소가 아닌 별도 배치로 돌리면 이전 값을 이미 잃은 뒤다. 반드시 upsert 시점에 비교한다.
