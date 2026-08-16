# 실행 로드맵 — 최소 기능부터 단계적 확장

> 작성일: 2026-08-06
> 근거: `plans/01`~`13` (영역별 계획서), `spec.md` §6 Phase, `docs/02_decision.md`

## 1. 이 문서의 위치

기존 계획서 14종은 **영역별(수직) 설계서**다. 각각은 해당 영역을 끝까지 다루므로,
그대로 따라가면 "모든 영역을 완성한 뒤에야 처음 화면이 뜨는" 순서가 된다.

이 문서는 그 계획서들을 **동작하는 얇은 조각(수평)으로 다시 자른 실행 순서**다.
설계 내용을 대체하지 않는다 — 각 단계는 기존 계획서의 어느 절을 어디까지 구현할지 지정한다.

```
기존 계획서:  02 도메인 ─── 04 어댑터 ─── 06 저장소 ─── 08 API ─── 11 UI
                (전체)      (전체)       (전체)      (전체)    (전체)

이 로드맵:    Step 1 ├─02의 일부─┼─04의 일부─┼─06의 일부─┼─08의 일부─┼─11의 일부┤ → 동작
              Step 2 └────────── 실제 vCenter에 적용하고 실측 ──────────┘
              Step 3 ├─────────── 위에서 얻은 사실로 다음 조각 ─────────┤ → 동작
```

## 2. 원칙

1. **매 단계가 끝나면 브라우저에서 결과를 볼 수 있어야 한다.** 중간 산출물만 남는 단계를 만들지 않는다.
2. **Step 1이 끝나면 즉시 실제 vCenter에 붙인다.** 계획서의 `[검증 필요]` 항목은 실환경 없이 닫히지 않는다.
3. **나중에 넣기 어려운 것만 처음부터 넣는다** (§4).
4. 단계마다 **범위 밖 항목을 명시**한다. "나중에"가 아니라 "이 단계에서는 하지 않는다"로 못 박는다.

## 3. 단계 개요

| Step | 이름 | 목표 | 관련 spec Phase |
|---|---|---|---|
| **1** | **MVP — 인증·계정 + vCenter 1개, VM 목록** | 화면 디자인 확정 + 로그인·가입·사용자 관리 + 등록 → 수집 → 조회 관통 | **Phase 0** + Phase 1(축소) + Phase 3(인증) |
| **2** | **실환경 적용·실측** | 계획서의 가정을 실제 vCenter로 검증 | — |
| 3 | 다중 연결 + 자동 수집 | 주기 수집, 부분 실패, 신선도 | Phase 1 |
| 4 | VM 상세 속성 + 검색 | IP·디스크·스냅샷, IP 역조회, 상세 화면 | Phase 2 |
| 5 | Hyper-V 어댑터 | 통합 인벤토리 (조직 우선순위에 따라 3 직후로 앞당길 수 있음) | Phase 1 |
| 6 | Host/Cluster/Datastore/Network | 자원 확장, 관계 탐색 | Phase 2 |
| 7 | 메타데이터 + 변경 이력 + 데이터 품질 | CMDB 기능 완성 | Phase 3 |
| 8 | 리포트·내보내기·대시보드 + 외부 API | 외부 연동, API 키, 감사 조회 화면 | Phase 4 |
| 9 | **폴스타 연동 — 게스트 관점 보강** | 게스트 OS 상세 보강 + 메타데이터 제안 (계획 14, D-019) | — (신규 요건) |

> **인증은 Step 1에 있다** (D-014). 사용자 요구이자, `AccessScope`가 저장소 조회 메서드의
> 첫 인자로 들어가기 때문에 나중에 넣으면 전 시그니처를 고쳐야 한다 (§4.1).

**그래도 Step 2(실환경 검증) 전에는 사내 전체에 공개하지 않는다.** 인증이 있어도
수집 데이터의 정확성이 확인되지 않은 상태이며, 인벤토리 정보는 그 자체로 공격 표면 정보다 (NFR-206).
Step 1~2는 **관리자 계정 1~2개로 운영**하고, 가입 승인은 Step 2 완료 후 시작한다.

---

## 4. 처음부터 지켜야 하는 것 / 나중에 넣어도 되는 것

MVP에서 무엇을 빼도 되는지를 가르는 기준은 "기능의 크기"가 아니라 **나중에 추가할 때의 파괴력**이다.

### 4.1 Step 1부터 반드시 지킨다

| 항목 | 근거 | 나중에 넣으면 생기는 일 |
|---|---|---|
| **CI 식별 규칙 + `uq_vm_native` 제약** | D-006, 계획 02 §7, 06 §2.7 | 중복 레코드가 쌓인 뒤 정리해야 한다. 재수집마다 늘어난다 |
| **`resource_identities` 테이블 + 식별키 조회 구조** | D-011, 계획 06 §2.7·§3.2 | `ON CONFLICT` 기반으로 만들면 2·3순위 추가 시 **upsert 전면 재작성**이다 (§7.3) |
| **`guest_observed_at` + 게스트 값 폴백** | D-011, 계획 02 §5.1, 06 §3.3 | 도구가 꺼진 VM을 재수집하는 순간 **이전 값이 NULL로 덮어써진다. 복구 불가** |
| **API 응답 계약** (`offset`/`limit`, 중첩 `guest`) | 계획 07 §2, 08 §3.1·6.3 | 나중에 바꾸면 API와 UI를 **동시에** 고쳐야 한다 (§9.2) |
| **`pg_trgm` 확장** | 계획 06 §2.1 | 확장 생성은 상위 DB 권한이 필요하다. Step 4에서 처음 부딪히면 **승인 대기로 막힌다** |
| **자격증명 AES-GCM 암호화** | NFR-203·208, 계획 10 §3 | 평문 비밀번호가 DB·백업·로그에 남는다. 사후 회수 불가 |
| **PropertyCollector 페이징** | D-007, 계획 04 §4 | 개별 순회로 만들면 어댑터 전면 재작성. 대규모 환경에서 타임아웃 |
| **Protocol 주입 (`HypervisorInventoryReader`)** | D-003, 계획 03 | 유스케이스에 pyVmomi가 스며들면 Hyper-V 추가 시 분기가 번진다 |
| **`GuestInfo` 3분법** (값 없음 / 수집 불가 / 미지원) | FR-501, 계획 02 §5.1 | 빈 문자열로 저장하면 "Tools 미설치"와 "값 없음"을 영원히 구분 못 한다 |
| **계층 구조 + `arch_check` 통과** | D-002 | 역방향 의존이 누적된 뒤 되돌리기 어렵다 |
| **UTC 저장** | 공통 규칙 3.4 | 로컬 시각으로 저장된 데이터는 사후 보정이 부정확하다 |
| **읽기 전용 (쓰기 API 미호출)** | D-005, CST-01 | 제품의 정체성이자 보안 경계다. 예외를 한 번 열면 근거가 무너진다 |
| **디자인 토큰 확정** (`docs/03_design_system.md`) | D-009, FR-1212 | 화면마다 다른 색·간격이 쌓인 뒤 통일하려면 전 화면을 다시 만들어야 한다 |
| **`AccessScope`가 저장소 조회의 첫 인자** | D-014, 계획 09 §3.1 | 나중에 넣으면 **모든 조회 메서드 시그니처와 호출부**를 고쳐야 한다. 누락된 경로 하나가 권한 우회다 |
| **계정 상태 4분기** (`pending`/`active`/`disabled`/`rejected`) | D-014, 계획 09 §2.1 | `is_active` 불리언으로 시작하면 "승인 대기"를 표현할 수 없어 스키마와 로그인 흐름을 다시 짠다 |
| **감사 로그 테이블** | FR-1004, 계획 10 §6 | **이력은 소급되지 않는다.** 누가 언제 연결을 등록·삭제했는지가 영영 남지 않는다 |

### 4.2 Step 1에서 뺀다

| 항목 | 나중에 추가하는 비용 |
|---|---|
| 외부 인증(LDAP/SSO), API 키 | `AuthProvider` Protocol로 분리해 두면 자격증명 검증만 위임하면 된다 (계획 09 §4.4) |
| 감사 로그 **조회 화면** | 기록은 Step 1부터 쌓고, 조회 UI만 Step 8로 미룬다 |
| 자동 수집 스케줄 | 수동 수집 유스케이스를 그대로 호출하는 워커를 얹으면 된다 |
| Host/Cluster/Datastore/Network | 테이블·어댑터 메서드 추가. VM 테이블은 안 바뀐다 |
| IP·디스크·스냅샷 | **자식 테이블 추가**라 기존 데이터 마이그레이션이 없다 |
| 메타데이터·변경 이력 | 별도 테이블. 수집 경로와 분리되어 있다 (FR-602). **단, 이력은 소급되지 않는다** — 아래 참조 |
| 검색·리포트·대시보드 | 조회 계층에만 얹힌다 |
| Redis 캐시 | 데이터 규모가 확인된 뒤 판단하는 것이 옳다 |

> **변경 이력의 시작점은 Step 8이다.** 계획 12의 diff는 upsert 시점에 생성되므로
> Step 1~7 동안 일어난 vCPU 변경·vMotion·이름 변경은 **영원히 기록되지 않는다.**
> 테이블은 나중에 추가할 수 있지만 지나간 변경은 되살릴 수 없다.
> 이력이 Step 8 이전부터 필요하다면 Step 3(수집 스케줄러)에서 `resource_changes`를 함께 도입한다.

> **화면 디자인은 여기에 없다.** Claude Design 프로세스는 **Step 1에 포함**된다 (§11).
> D-009가 정한 "구현 전 디자인 확정"은 MVP에도 그대로 적용된다.

### 4.3 조회 범위(scope) — Step 1부터 넣는다

계획 09 §3.1은 저장소 조회 메서드의 **첫 인자를 `scope`로 강제**한다.
인증이 Step 1로 들어오면서 이 인자도 처음부터 자리를 잡는다 (D-014).

```python
# 계획 09 §3.1·계획 07 §2의 시그니처를 그대로 쓴다. 나중에 바꾸지 않는다.
async def list_virtual_machines(
    self, scope: AccessScope, criteria: SearchCriteria, page: Page
) -> PagedResult[VmSummary]: ...
```

```sql
-- 범위는 반드시 SQL에 들어간다. 조회 후 파이썬에서 거르면 페이징 건수가 어긋난다.
AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
```

**범위 없는 전체 조회 함수를 만들지 않는다.** 존재하면 언젠가 쓰이고, 그 경로가 권한 우회가 된다.
수집 워커처럼 사용자 컨텍스트가 없는 곳은 `AccessScope.system()`을 명시적으로 쓴다.

---

# Step 1. MVP — 인증·계정 + vCenter 1개 등록, VM 목록 조회

> **구현 완료 (2026-08-07).** §13 완료 기준은 §13.1 대조표를 참조한다.
> 실제 vCenter 연결은 Step 2에서 수행한다 — 지금까지의 검증은 목 커넥터와 로컬 PostgreSQL 기준이다.
> **실서버 배포 절차는 `docs/05_deployment.md`에 있다.** §15.4의 DB 권한 항목(17·18)은
> 그 문서 §4.2에 최소 권한 레시피로 정리되어 있으며 비-superuser 계정으로 검증했다.

## 5. 목표와 범위

Step 1은 **디자인 트랙(1-A)과 백엔드 트랙(1-B)을 병렬로 진행**하고, 둘이 만나는 지점에서 UI를 구현한다(1-C).

```
1-A 디자인   ├─ Claude Design 컨텍스트 주입 → 자원 목록 → 연결 관리 → 검토 → 핸드오프 ─┐
   (§10)     │                                                                        │
             │                                              docs/03_design_system.md ─┤
1-B 백엔드   ├─ 도메인 → 암호화 → DB → 어댑터 → 수집 → API ─────────────────────────────┤
   (§12)     │                                                                        ▼
1-C UI 구현  └────────────────────────────────────────────────────── static/ 구현 (§11)
```

**1-A는 백엔드 구현과 무관하므로 Step 1 착수와 동시에 시작한다** (계획 11 §2).
디자인이 늦어지면 1-C가 막히므로, 1-A를 나중으로 미루지 않는다.

### 5.1 완료 시 가능한 것

```
[관리자]
1. 부트스트랩 관리자 계정으로 로그인
2. /admin/connections 에서 vCenter 등록 → [연결 테스트] → [저장]
3. [지금 수집] → 수집 완료 확인
4. 가상 머신 목록에서 수집 결과 확인
   (이름 · 전원 상태 · vCPU · 메모리 · 게스트 OS · 소속 호스트 · 최종 수집 시각)
5. [지금 수집]을 다시 눌러도 VM 레코드가 중복되지 않음

[신규 사용자]
6. /register 에서 가입 신청 → "관리자 승인 후 로그인" 안내
7. 승인 전 로그인 시도 → 거부됨. 조회 API도 호출 불가
8. 관리자가 /admin/users 에서 [승인] → 역할(조회자)과 조회 범위(vCenter 1개) 부여
9. 신규 사용자가 로그인 → 부여된 범위의 VM만 보임
10. 관리자가 범위를 비우면 → 같은 사용자에게 아무것도 보이지 않음 (기본 거부)
```

### 5.2 범위 밖 (이 단계에서 하지 않는다)

외부 인증(LDAP/SSO) / API 키 / 감사 로그 **조회 화면**(기록은 함)/
자동 수집 스케줄 / Hyper-V / 검색·필터 / VM 상세 화면 /
IP·MAC·디스크·스냅샷 수집 / Host·Cluster·Datastore·Network 자원 /
메타데이터 입력 / 변경 이력 / 감사 로그 / 리포트·내보내기 / 대시보드 / Redis /
연결 수정(PATCH)

**화면 디자인은 자원 목록·연결 관리 2개만** 한다. 자원 상세·대시보드·검색 결과 화면의
디자인은 해당 기능을 구현하는 Step에서 같은 캔버스에 이어서 만든다 (§10.3).

> **연결 수정을 빼는 이유**: 주소 변경 경고(FR-110·111)와 비밀번호 부분 갱신(FR-108)이 얽혀 있어
> 계획 08 §5.5 전체가 딸려 온다. MVP에서는 **삭제 후 재등록**으로 대체한다.

### 5.3 VM 목록에 표시하는 필드

| 컬럼 | 출처 | 비고 |
|---|---|---|
| 이름 | `name` | |
| 전원 상태 | `runtime.powerState` | 정규화 (계획 02 §9.1) |
| vCPU | `config.hardware.numCPU` | |
| 메모리 | `config.hardware.memoryMB` | GB 환산 표시 |
| 게스트 OS | `guest.guestFullName` → 없으면 `config.guestFullName` | **출처 표시 필수** (FR-304) |
| 소속 호스트 | `runtime.host` MoRef | Step 6까지는 MoRef 문자열 그대로 |
| 최종 수집 | `last_seen_at` | |

**게스트 OS 컬럼이 이 MVP의 시금석이다.** Tools 미설치 VM에서 빈 칸이 아니라
`수집 불가 — VMware Tools 미설치`로 표시되어야 한다 (FR-501, 계획 11 §14.2).
이것이 되면 "값 없음 / 수집 불가" 구분 설계가 파이프라인 전 구간에서 살아 있다는 뜻이다.

## 6. 구현 대상 파일

계획 01 §2 구조를 따르되, 이 단계에서 만드는 파일만 나열한다.

```
src/
├── config.py                        계획 01 §4 축소 — DB·암호화 키·수집 타임아웃만
├── main.py                          FastAPI 앱, static 마운트, 라우터 등록
├── domain/
│   ├── enums.py                     PowerState, ConnectionState, GuestInfoAvailability,
│   │                                OsSource, HypervisorKind  ← 계획 02 §3에서 필요분만
│   ├── values.py                    GuestInfo, CpuSpec, MemorySpec  ← 계획 02 §5
│   ├── resource.py                  VirtualMachine  ← 계획 02 §6.2 (필드 축소)
│   ├── connection.py                Connection  ← 계획 02 §10 (vCenter만)
│   ├── identity.py                  IdentityRule, IdentityKey, build_vm_identity_keys
│   │                                ← 계획 02 §7. **1순위만 생성**하되 함수 형태는 그대로
│   ├── ports.py                     HypervisorInventoryReader (축소, §8)
│   └── exceptions.py                계획 02 §4 전체 — 예외 계층은 처음부터 완성
├── utils/
│   └── retry.py                     계획 02 §4.1 — 인증 실패는 재시도 금지
├── infrastructure/
│   ├── security/cipher.py           계획 10 §3 전체 — 축소하지 않는다
│   ├── db/                          engine, session, ORM 모델 3개
│   ├── repository/
│   │   ├── connection_repo.py
│   │   └── vm_repo.py               upsert (계획 06 §3 구조 그대로 — §7.3), 목록 조회
│   └── vcenter/
│       ├── session.py               계획 04 §3 전체
│       ├── collector.py             계획 04 §4 전체 — 페이징 포함
│       ├── property_specs.py        §7.2의 축소 목록
│       ├── mapper.py                계획 04 §6.1·6.2 (장치 매핑 제외)
│       ├── errors.py                계획 04 §8 전체
│       └── reader.py                Protocol 구현
├── application/
│   ├── connection_service.py        등록·삭제·연결 테스트
│   ├── collect_service.py           수집 실행 (계획 06 §7 축소)
│   └── inventory_query.py           VM 목록 조회 (계획 07 §3 축소)
└── api/
    ├── deps.py                      DB 세션·reader 팩토리 DI
    ├── schemas/                     요청·응답 모델
    └── routes/
        ├── health.py
        ├── connections.py
        └── virtual_machines.py
static/
├── index.html                       VM 목록
├── connections.html                 연결 관리
├── css/base.css
└── js/{api.js, resources.js, connections.js}
migrations/                          Alembic 초기 리비전
tests/
├── fakes/fake_reader.py             계획 03 §8
├── unit/                            매퍼·암호화·식별 규칙
└── integration/test_collect.py      2회 수집 → 1건
```

**`src/orchestration/`은 Step 1에 없다.** 수동 수집은 API 요청으로 시작하므로
`application/collect_service.py`로 충분하다. 워커는 Step 3에서 만든다.

## 7. DB 스키마 (Step 1)

계획 06 §2의 축소판이다. **컬럼은 줄이되 제약과 식별 구조는 줄이지 않는다.**

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 실제 사용은 Step 4(검색). 권한 확인을 앞당기려 지금 만든다

CREATE TABLE connections (
    connection_id       UUID PRIMARY KEY,
    kind                TEXT NOT NULL DEFAULT 'vcenter',   -- Step 5에서 hyperv 추가 + DEFAULT 제거
    display_name        TEXT NOT NULL,
    address             TEXT NOT NULL,
    port                INTEGER NOT NULL DEFAULT 443,
    username            TEXT NOT NULL,
    password_encrypted  TEXT NOT NULL,          -- {key_version}:{nonce}:{ciphertext} (계획 10 §3.1)
    verify_tls          BOOLEAN NOT NULL DEFAULT true,
    last_attempt_at     TIMESTAMPTZ,
    last_success_at     TIMESTAMPTZ,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_connection_target UNIQUE (address, username)   -- FR-105, DB 레벨 강제
);

CREATE TABLE virtual_machines (
    resource_id         UUID PRIMARY KEY,
    connection_id       UUID NOT NULL REFERENCES connections(connection_id) ON DELETE RESTRICT,
    native_id           TEXT NOT NULL,          -- config.instanceUuid
    bios_uuid           TEXT,                   -- config.uuid — Step 5의 교차 식별 대비
    name                TEXT NOT NULL,
    power_state         TEXT NOT NULL DEFAULT 'unknown',
    connection_state    TEXT NOT NULL DEFAULT 'unknown',
    vcpu_count          INTEGER,
    memory_mb           BIGINT,
    configured_os       TEXT,
    guest_availability  TEXT NOT NULL DEFAULT 'unknown',   -- FR-501 3분법
    guest_os_name       TEXT,
    guest_os_source     TEXT,                   -- guest_tools | vm_config (FR-304)
    guest_hostname      TEXT,
    guest_observed_at   TIMESTAMPTZ,            -- 게스트 값이 실제 수집된 시각 (§7.4)
    host_native_id      TEXT,
    lifecycle           TEXT NOT NULL DEFAULT 'active',
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_vm_native UNIQUE (connection_id, native_id)    -- CI 식별 1순위 (FR-303)
);

CREATE INDEX idx_vm_conn_active ON virtual_machines (connection_id) WHERE lifecycle = 'active';

-- 계획 06 §2.7 그대로. Step 1은 rule=1만 기록한다 (§7.3)
CREATE TABLE resource_identities (
    resource_id         UUID NOT NULL,
    resource_type       TEXT NOT NULL,
    connection_id       UUID NOT NULL,
    rule                SMALLINT NOT NULL,      -- 1(native) | 2(bios_uuid) | 3(mac+name)
    key_value           TEXT NOT NULL,
    PRIMARY KEY (rule, key_value, resource_id)
);
CREATE INDEX idx_identities_lookup ON resource_identities (rule, key_value);
CREATE INDEX idx_identities_resource ON resource_identities (resource_id);

-- ── 계정 (D-014) — 계획 09 §5 그대로. 상태 4분기와 CHECK 제약을 줄이지 않는다 ──
CREATE TABLE users (
    user_id            UUID PRIMARY KEY,
    username           TEXT UNIQUE NOT NULL,       -- 소문자 정규화
    password_hash      TEXT NOT NULL,
    display_name       TEXT,
    email              TEXT,
    role               TEXT NOT NULL DEFAULT 'viewer',
    status             TEXT NOT NULL DEFAULT 'pending',
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    reject_reason      TEXT,
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       TIMESTAMPTZ,
    last_login_at      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_users_status CHECK (status IN ('pending', 'active', 'disabled', 'rejected')),
    CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'operator', 'admin'))
);
CREATE INDEX idx_users_pending ON users (created_at) WHERE status = 'pending';

CREATE TABLE user_connection_scopes (
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE CASCADE,
    granted_by    TEXT,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, connection_id)
);
CREATE INDEX idx_scopes_user ON user_connection_scopes (user_id);

-- 감사 기록 (FR-1004). 조회 화면은 Step 8이지만 기록은 지금부터 — 이력은 소급되지 않는다
CREATE TABLE audit_events (
    event_id     BIGSERIAL PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor        TEXT,                              -- username. 삭제되지 않으므로 항상 추적 가능
    action       TEXT NOT NULL,
    target_type  TEXT,
    target_id    TEXT,
    result       TEXT NOT NULL,                     -- success | failure
    client_ip    INET,
    detail       JSONB                              -- 화이트리스트 키만 (계획 10 §6.2)
);
CREATE INDEX idx_audit_occurred ON audit_events (occurred_at DESC);
CREATE INDEX idx_audit_actor ON audit_events (actor, occurred_at DESC);
```

**`connections`를 `user_connection_scopes`가 참조하므로 마이그레이션 순서는 `connections` → `users` → `scopes`다.**

**`lifecycle` 컬럼을 Step 1에 두는 이유**: 값은 항상 `'active'`이고 미발견 처리(FR-307)는
Step 3에서 구현하지만, **인덱스 조건에 들어가므로** 나중에 추가하면 부분 인덱스를 전부 다시 만들어야 한다.

### 7.1 이 단계에서 만들지 않는 테이블

계획 06·09·10·12에 정의된 테이블 중 Step 1에 **없는 것 전부**다. 목록에 없는 테이블이 생기면 범위 이탈이다.

| 테이블 | 도입 Step | 정의 위치 |
|---|---|---|
| `vm_disks`, `vm_adapters`, `vm_adapter_ips`, `snapshots` | 4 | 계획 06 §2.4·2.5 |
| `hosts`, `clusters`, `datastores`, `networks` | 6 | 계획 06 §2.5 |
| `collection_runs`, `collection_outcomes` | 3 | 계획 06 §2.8 |
| `resource_metadata` | 8 | 계획 06 §2.6 |
| `resource_changes` | 8 (필요 시 3) | 계획 12 §6 |
| `duplicate_candidates` | 5 | 계획 12 §8 |
| `api_keys` | 8 | 계획 09 §5 |

모두 **자식/독립 테이블**이라 나중에 `CREATE TABLE` 한 번으로 추가된다. `virtual_machines`는 건드리지 않는다.
`resource_identities`만 예외적으로 Step 1에 만든다 — 이유는 §7.3.

### 7.2 수집 속성 (Step 1)

계획 04 §5 `VM_PROPERTIES`의 축소판이다.

```python
VM_PROPERTIES_MVP: list[str] = [
    "name",
    "config.instanceUuid",              # native_id — CI 식별 1순위
    "config.uuid",                      # bios_uuid — 2순위 (Step 5 대비 지금부터 저장)
    "config.guestFullName",             # 구성값 OS
    "config.hardware.numCPU",
    "config.hardware.memoryMB",
    "runtime.powerState",
    "runtime.connectionState",
    "runtime.host",
    "guest.guestFullName",              # 도구 감지값 OS
    "guest.hostName",
    "guest.toolsStatus",
    "guest.toolsRunningStatus",
]
```

**`config.hardware.device`를 뺀 것이 핵심이다.** 이 속성이 응답 크기의 대부분을 차지한다.
디스크·NIC를 다루지 않는 Step 1에서는 제외하여 수집 시간을 실측하기 좋은 조건을 만든다.
Step 4에서 추가할 때 **같은 환경에서 전후 소요 시간을 비교**하면 이 속성의 비용을 정량화할 수 있다.

### 7.3 Upsert — 계획 06 §3의 구조를 그대로 쓴다

**`INSERT ... ON CONFLICT (connection_id, native_id) DO UPDATE`로 만들지 않는다.**
가장 짧게 쓰는 방법이지만, 2·3순위 식별(BIOS UUID, MAC+이름)을 추가하는 순간
"제약 위반을 잡아 갱신"에서 "식별키로 찾아 분기"로 **알고리즘 자체가 바뀐다.** Step 4·5에서 upsert 전면 재작성이다.

Step 1은 계획 06 §3.1의 흐름을 그대로 구현하되 **키 생성만 1순위로 제한한다.**

```python
def build_vm_identity_keys(vm: VirtualMachine) -> list[IdentityKey]:
    """계획 02 §7. Step 1은 1순위만 반환한다.
    2순위(bios_uuid)는 Step 5, 3순위(MAC+이름)는 Step 4에서 이 함수에 행을 더한다.
    """
    return [IdentityKey(IdentityRule.NATIVE, f"{vm.connection_id}:{vm.native_id}")]
```

```python
# vm_repo.py — 계획 06 §3.1·3.2와 같은 골격
keys = build_vm_identity_keys(vm)
match = await self._find_by_identity(connection_id, keys)   # limit(2) 모호성 감지 포함
if match is None:
    resource_id = await self._insert_vm(vm, observed_at)
else:
    resource_id = match.resource_id
    merged = _merge_guest(vm, await self._load_vm(resource_id))   # §7.4
    await self._update_vm(resource_id, merged, observed_at)
await self._sync_identities(resource_id, keys, connection_id)
```

Step 4·5에서 늘어나는 것은 `build_vm_identity_keys`가 반환하는 **행의 수뿐이다.**
`DuplicateCandidate` 분기(계획 06 §3.1의 `match.connection_id != connection_id`)는
연결이 하나뿐인 Step 1에서 발생할 수 없으므로 넣지 않는다. Step 3(다중 연결)에서 추가한다.

> `uq_vm_native` 제약은 그대로 유지한다. 식별키 조회가 논리적 방어이고, 제약은 **로직에 버그가 있어도
> 중복이 물리적으로 생기지 않게 하는** 최후 방어다. 둘 다 있어야 한다.

### 7.4 게스트 값 폴백 — 덮어쓰면 되돌릴 수 없다

VMware Tools가 꺼진 VM을 재수집할 때 `guest.*`는 전부 비어서 온다.
이를 그대로 저장하면 **직전 수집에서 얻은 게스트 OS·호스트명이 NULL로 덮어써진다.**
테이블은 나중에 고칠 수 있지만 **덮어쓴 값은 복구할 수 없으므로** Step 1부터 넣는다.

```python
def _merge_guest(incoming: VirtualMachine, previous: VirtualMachine) -> VirtualMachine:
    """계획 06 §3.3. 도구가 멈춘 것이지 VM의 OS가 사라진 것이 아니다."""
    return replace(incoming, guest=incoming.guest.with_fallback(previous.guest))
```

`GuestInfo.with_fallback`(계획 02 §5.1)은 이전 값과 함께 **원래 관측 시각(`observed_at`)을 유지**한다.
이것이 `guest_observed_at` 컬럼이 Step 1에 필요한 이유이며,
계획 11 §5.1이 요구하는 `마지막 확인: … (3일 전)` 표시의 데이터 근거다.

Step 1 UI는 이 시각을 표시하지 않아도 되지만, **값은 그때부터 쌓여 있어야 한다.**

## 8. Protocol (Step 1 축소판)

계획 03 §2의 전체 Protocol 중 이 단계에 필요한 것만 선언한다.
**빈 구현으로 채운 메서드를 미리 두지 않는다** — 계약 테스트가 껍데기가 되고, 미지원과 미구현이 섞인다.

```python
# src/domain/ports.py
@runtime_checkable
class HypervisorInventoryReader(Protocol):
    """하이퍼바이저 인벤토리 조회 인터페이스.

    구현체는 조회만 수행하며 자원을 변경하는 API를 호출하지 않는다 (CST-01, D-005).

    Step 1 범위: VM 목록과 연결 검증. 나머지 list_*는 Step 4~6에서 추가한다.
    """

    @property
    def connection_id(self) -> UUID: ...

    async def start_session(self) -> None: ...
    async def close_session(self) -> None: ...
    async def __aenter__(self) -> "HypervisorInventoryReader": ...
    async def __aexit__(self, *exc_info: object) -> None: ...

    async def check_connection(self) -> ConnectionCheckResult: ...

    def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]: ...
```

- `list_*`를 `async def`로 선언하지 않는 이유는 계획 03 §2.1과 같다.
- `ReaderCapabilities`·`get_outcomes()`는 **Step 3**에서 도입한다 (하이퍼바이저가 하나뿐이고
  자원 유형도 하나뿐인 동안에는 판정할 대상이 없다).
- `ConnectionCheckResult`는 계획 03 §4를 그대로 쓴다. 4단계 판정(도달·TLS·인증·권한)은
  MVP에서 가장 자주 마주칠 실패를 구분해주므로 축소하지 않는다.

## 9. API (Step 1)

| 메서드 | 경로 | 권한 | 동작 |
|---|---|---|---|
| GET | `/api/v1/health` | 공개 | DB 연결 확인 |
| POST | `/api/v1/auth/register` | 공개 | 가입 신청 → **202** (중복이어도 동일 응답) |
| POST | `/api/v1/auth/login` | 공개 | 로그인 → `httpOnly` 쿠키 (D-014) |
| POST | `/api/v1/auth/logout` | 인증 | 쿠키 삭제 |
| GET | `/api/v1/auth/me` | 인증 | 사용자 + `permissions[]` — **UI 메뉴 판단용** (FR-1213) |
| POST | `/api/v1/auth/change-password` | 인증 | 현재 비밀번호 확인 필수 |
| GET | `/api/v1/users` | admin | 목록. `status` 필터 |
| POST | `/api/v1/users/{id}/approve` | admin | **역할 + 조회 범위를 함께 부여** |
| POST | `/api/v1/users/{id}/reject` | admin | |
| POST | `/api/v1/users/{id}/disable` `/enable` | admin | 삭제 엔드포인트는 없다 (D-014) |
| PATCH | `/api/v1/users/{id}` | admin | 역할·표시이름 |
| PUT | `/api/v1/users/{id}/scopes` | admin | 조회 범위 교체 |
| POST | `/api/v1/users/{id}/reset-password` | admin | 임시 비밀번호 **1회만 반환** |
| POST | `/api/v1/connections/test` | admin | 저장 전 연결 테스트 (FR-106) |
| POST | `/api/v1/connections` | admin | 등록. 비밀번호 암호화 저장 |
| GET | `/api/v1/connections` | admin | 목록 (**비밀번호 필드 없음**) |
| DELETE | `/api/v1/connections/{id}` | admin | 삭제. 수집된 VM이 있으면 409 |
| POST | `/api/v1/connections/{id}/collect` | admin | 수집 시작 → **202 Accepted** |
| GET | `/api/v1/virtual-machines` | 인증 | 목록. `connection_id`, `offset`, `limit`, `sort_by`, `sort_desc` (§9.2). **`AccessScope`로 필터** |

**범위 밖 `connection_id`를 받으면 403이 아니라 빈 결과를 준다.**
403은 "그 연결이 존재한다"는 사실을 알려준다.

**공개 경로는 화이트리스트로 관리한다** (계획 08 §4.2). 새 엔드포인트는 기본이 인증 필요이며,
공개가 필요하면 명시적으로 추가한다. 반대로 만들면 인증 누락이 조용히 생긴다.

### 9.1 수집을 202로 반환하는 이유

수천 건 수집은 수 분이 걸릴 수 있다. 동기 응답으로 만들면 프록시·브라우저 타임아웃에 걸리고,
그때 **수집은 계속 진행 중인데 UI는 실패로 표시**되어 원인 파악이 어려워진다.

```python
@router.post("/{connection_id}/collect", status_code=202)
async def start_collection(connection_id: UUID, background: BackgroundTasks, ...):
    await service.mark_attempt(connection_id)          # last_attempt_at 갱신
    background.add_task(service.collect, connection_id)
    return {"status": "accepted"}
```

UI는 `GET /api/v1/connections`를 폴링하여 `last_success_at`·`last_error`로 완료를 판정한다.
`collection_runs` 테이블은 Step 3에서 도입한다.

> **DELETE에서 409를 반환하는 이유**: `ON DELETE RESTRICT`가 이미 막지만, DB 에러를 그대로
> 500으로 흘리면 원인을 알 수 없다. FR-109의 2단계 확인은 Step 3에서 구현한다.

### 9.2 응답 계약은 최종 형태로 만든다

페이징과 게스트 표현을 Step 1 편의대로 만들면 Step 4에서 API와 UI를 동시에 고치게 되고,
그때는 화면이 더 늘어나 있다. **계약은 지금 최종 형태로 맞춘다.**

**페이징** — 계획 07 §2 `Page`·`PagedResult`, 계획 08 §3.1 `PagedResponse`를 그대로 쓴다.

```python
class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int          # page/size가 아니다. 계획 07 Page(offset, limit, sort_by, sort_desc)
    limit: int
    total_is_estimate: bool = False
```

정렬 컬럼은 Step 1부터 **화이트리스트(`Page.ALLOWED_SORT`)로 검증**한다. 문자열을 그대로 SQL에
넣는 구현을 한 번 만들면 인젝션 경로가 남는다.

**게스트 정보** — 평면 필드가 아니라 계획 08 §6.3의 중첩 객체로 내려보낸다.

```python
class GuestInfoResponse(BaseModel):
    availability: GuestInfoAvailability
    is_collected: bool
    unavailable_reason: str | None       # UNAVAILABLE_REASONS 매핑 (계획 02 §5.1)
    os_name: str | None
    os_source: OsSource | None
    hostname: str | None
    observed_at: datetime | None         # §7.4
    # ipv4_addresses / ipv6_addresses는 Step 4에서 이 객체에 추가된다
```

**사유 문자열을 서버가 만든다**는 점이 중요하다. `unavailable_reason`을 응답에 넣으면
UI는 분기 없이 그대로 출력하면 되고, 표현 문구가 여러 화면에 흩어지지 않는다 (§11.1).

## 10. 화면 디자인 (1-A) — Claude Design

D-009에 따라 **코드 작성 전에 화면을 디자인해 확정한다.** 계획 11 Part A(§3~§9)를 Step 1 범위로 수행한다.
백엔드(1-B)와 병렬로 진행하되, **Step 1 착수와 동시에 시작한다.**

### 10.1 착수 전 확인 — 막히면 Step 1 전체가 지연된다

| 확인 항목 | 실패 시 |
|---|---|
| Claude Design 플랜 (Pro/Max/Team/Enterprise) | 계획 11 §10 대체 경로(`artifact-design` 스킬)로 전환 |
| Enterprise인 경우 관리자 활성화 여부 | 관리자 요청 → 승인 대기가 길면 대체 경로 |
| 시안 검토자 지정 (운영팀 담당자) | 검토 없이 확정하면 Step 3 이후 재작업 |

> **대체 경로로 전환하더라도 §10.2의 컨텍스트 주입과 §10.5의 검토는 동일하게 수행한다.**
> 도구가 아니라 **읽기 전용 원칙과 "수집 불가" 표현을 시안 단계에서 못 박는 것**이 이 단계의 목적이다.

### 10.2 컨텍스트 주입 (계획 11 §5)

계획 11 §5.1의 최초 프롬프트를 **그대로** 사용한다. 특히 다음 두 블록은 축약하지 않는다.

- `[절대 넣지 말 것]` — 전원·삭제·스냅샷·마이그레이션 버튼 금지 (FR-1206, D-005)
- `[반드시 구분해서 표시할 것]` — 수집된 값 / 수집 불가 / 미지원 / 빈 값의 4분기 (FR-501·1204)

**목 데이터는 계획 11 §5.2 규격을 전부 포함한다.** Step 1에 없는 개념(Hyper-V 배지, 미발견 배지,
다중 IP, 소유자 메타데이터)도 포함시킨다.

> **Step 1에 없는 데이터까지 디자인하는 이유**: 토큰과 컴포넌트 규격은 최종 형태를 보고 정해야 한다.
> VM 8행짜리 이상적인 표로 색과 밀도를 정하면, Step 4에서 IP 3개짜리 행과 미발견 배지가 들어오는 순간
> 전 화면을 다시 맞춰야 한다. **시안은 넓게, 구현은 좁게** 한다.

Step 1에서 구현하는 것은 §5.3의 7개 컬럼뿐이며, 나머지는 시안으로만 보관한다.

### 10.3 화면 범위와 순서

계획 11 §6의 5개 화면 중 Step 1은 2개다. **순서를 바꾸지 않는다.**

| 순서 | 화면 | 여기서 확정되는 것 | Step 1 구현 |
|---|---|---|---|
| **1** | **자원 목록** | **색상 토큰, 상태 배지, 표 밀도, 타이포그래피** | ✔ (컬럼 축소) |
| 2 | 연결 관리 | 폼 레이아웃, 확인 다이얼로그, 연결 상태 표시 | ✔ |
| 3 | **로그인 · 가입 신청** | 셸 없는 중앙 카드, 폼 오류 표현 (계획 11 §13.2) | ✔ |
| 4 | **사용자 관리** | 승인 대기 강조, 계정 상태 배지 4종, 승인 다이얼로그 (계획 11 §13.3) | ✔ |
| — | 자원 상세 / 대시보드 / 검색 결과 | — | Step 4·6·8에서 같은 캔버스에 이어서 |

**3·4번은 1·2번에서 확정된 토큰을 재사용한다.** 새 색이나 새 간격을 만들지 않는다.
계정 상태 배지는 자원의 4분기 표기(§2)와 **같은 아이콘·색 체계**를 쓴다.

**자원 목록을 먼저 확정한다.** 여기서 나온 토큰이 이후 모든 화면의 기준이 되며,
Step 1에 기존 디자인 자산이 없으므로(계획 11 §5.3) 사실상 이 화면이 디자인 시스템의 출발점이다.

계획 11 §6.1의 6개 체크 항목은 자원 목록 시안에서 **전부** 확인한다.
특히 `수집 불가 5행이 정상 27행 사이에서 눈에 띄는가` — 한 행만 보면 항상 구분되므로 30행 전체로 판단한다.

### 10.4 산출물 — `docs/03_design_system.md`

핸드오프 번들의 **실제 토큰 값**을 기록한다 (계획 11 §9 양식).

| 절 | Step 1 범위 |
|---|---|
| §1 디자인 토큰 | **전체 기록** — 색상·타이포·간격·radius |
| §2 상태 표현 규칙 | **전체 기록** — 수집 불가/미지원/빈 값/신선도 |
| §3 컴포넌트 규격 | Step 1 구현분(표·배지·폼·다이얼로그)만. 나머지는 시안 참조만 남긴다 |
| §4 프로젝트 제약 | §10.5 검토 결과 |
| §5 결정 이력 | 검토 피드백 반영 내역 |

### 10.5 핸드오프 직후 필수 검토 (계획 11 §8.3)

**자동으로 넘어오지 않는 것들이다. 이 검토를 건너뛰면 읽기 전용 위반이 코드까지 따라온다.**

```
□ 시안에 전원·삭제·스냅샷 조작 버튼이 없는가        ← 화면마다 확인 (FR-1206)
□ "수집 불가"가 빈 칸이나 "N/A"로 바뀌지 않았는가    ← 가장 자주 유실되는 규칙
□ 게스트 OS 출처 병기가 살아 있는가 (FR-304)
□ 색상만으로 상태를 전달하지 않는가 (아이콘·텍스트 병기)
□ 다크 모드 대비가 충분한가
□ 목 데이터에 실제 서버명·IP가 남아 있지 않은가      ← NFR-206
```

마지막 항목은 캔버스가 **내부 URL로 공유**되기 때문에 중요하다.
인벤토리 정보(IP·호스트명·OS)는 그 자체로 공격 표면 정보다.

## 11. UI 구현 (1-C)

빌드 체인 없는 정적 파일 + fetch (계획 11 §12 구성). **화면은 2개지만 임시 화면이 아니다.**
1-A에서 확정한 토큰·컴포넌트 규격을 구현하며, Step 3 이후 화면이 여기에 이어 붙는다.

```
static/
├── login.html          로그인 — 셸 없음
├── register.html       가입 신청 — 셸 없음
├── index.html          자원 목록(VM) — 표, 페이지 이동, 연결 선택
├── connections.html    연결 관리 — 등록 폼, 목록, [연결 테스트] [지금 수집] [삭제]
├── users.html          사용자 관리 — 승인 대기, 전체 목록, 승인 다이얼로그 (admin)
├── css/
│   ├── tokens.css      docs/03_design_system.md §1 = 핸드오프 번들 토큰 (§10.4)
│   ├── layout.css      셸 + 인증 화면 중앙 카드
│   └── components.css  표·배지·폼·다이얼로그
└── js/
    ├── api.js          fetch 래퍼, 401 처리, 에러 표시 (백엔드 완성 전에는 목 데이터)
    ├── format.js       수집 불가·용량·날짜 표시 (§11.1)
    ├── shell.js        사이드바·헤더·테마·**인증 가드** — 인증 화면 공통
    ├── resources.js
    ├── connections.js
    ├── users.js
    ├── login.js
    └── register.js
```

**인증 가드는 `shell.js`에 둔다.** 각 페이지가 알아서 확인하게 만들면 새 화면에서 빠뜨린다.
`GET /auth/me`가 401이면 `login.html`로 보내고, 권한이 없는 화면이면 자원 목록으로 되돌린다.

**`tokens.css`는 핸드오프 번들의 값을 그대로 옮긴다.** 눈대중으로 색을 고르면
Step 3 이후 화면과 어긋나고, 그때 전 화면을 다시 맞춰야 한다.

### 11.1 반드시 지킬 표시 규칙

계획 11 §14.2의 축소판. 1-A 시안에서 확정한 표현을 코드로 옮긴다.

```javascript
// 게스트 OS 셀 — 빈 값으로 두지 않는다 (FR-501)
// 사유 문구는 서버가 만든다 (§9.2). 표현을 UI에 하드코딩하면 화면마다 어긋난다.
function osCell(vm) {
  const g = vm.guest;                       // 중첩 객체 (계획 08 §6.3)
  if (!g.is_collected) {
    // 폴백으로 남아 있는 이전 값이 있으면 병기한다 (§7.4, 계획 11 §5.1)
    const last = g.os_name
      ? ` (마지막 확인: ${g.os_name}, ${formatRelative(g.observed_at)})`
      : '';
    return { text: `수집 불가 — ${g.unavailable_reason}${last}`, className: 'unavailable' };
  }
  if (!g.os_name)
    return { text: '—', className: 'empty' };
  // 출처 병기 (FR-304) — 구성값은 실제와 다를 수 있다
  const suffix = g.os_source === 'vm_config' ? ' (구성값)' : '';
  return { text: g.os_name + suffix, className: '' };
}
```

**자원을 변경하는 버튼을 만들지 않는다** (FR-1206, D-005).
전원·삭제·마이그레이션 조작 UI는 이 포탈에 존재하지 않는다.
`[지금 수집]`·`[삭제]`는 **포탈의 연결 레코드**에 대한 조작이며 하이퍼바이저를 변경하지 않는다.

## 12. 구현 순서

각 단계는 **검증 가능한 상태**로 끝난다. 앞 단계가 검증되기 전에 다음으로 넘어가지 않는다.

### 12.1 디자인 트랙 (1-A) — 백엔드와 병렬, Day 1 시작

| # | 작업 | 검증 방법 |
|---|---|---|
| A1 | 플랜·활성화·검토자 확인 (§10.1) | 사용 가능 확인 또는 대체 경로 결정 |
| A2 | 컨텍스트 주입 + 목 데이터 투입 (§10.2) | 프롬프트에 금지 목록·4분기 표현이 포함됨 |
| A3 | **자원 목록 시안** (§10.3) | 계획 11 §6.1 체크 6항목 통과 |
| A4 | 연결 관리 시안 | 조작 버튼 부재 재확인 |
| A4b | **로그인·가입 시안** (계획 11 §13.2) | 셸 없는 중앙 카드, 오류 문구가 계정 존재를 노출하지 않음 |
| A4c | **사용자 관리 시안** (계획 11 §13.3) | 승인 대기 강조, 상태 배지 4종, 승인 다이얼로그(역할+범위 동시) |
| A5 | 검토 → 피드백 반영 | 검토자 승인 |
| A6 | 핸드오프 → `docs/03_design_system.md` 작성 (§10.4) | 토큰 값이 문서에 기록됨 |
| A7 | 핸드오프 후 검토 (§10.5) | 6개 체크 항목 통과 |

### 12.2 백엔드 트랙 (1-B) → UI 구현 (1-C)

| # | 작업 | 검증 방법 |
|---|---|---|
| 1 | 스켈레톤 + `config.py` + `main.py` + health | `uvicorn src.main:app` → `/api/v1/health` 200 |
| 2 | `python scripts/arch_check.py --ci` 통과 | 위반 0건 |
| 3 | `domain/` (enums·values·resource·connection·**identity**·exceptions) | 단위 테스트: `GuestInfo` 3분법 분기, `with_fallback`이 이전 값·`observed_at` 유지 |
| 4 | `security/cipher.py` (계획 10 §3) | 암복호 왕복, 변조 시 `InvalidTag`, `__repr__`에 평문 없음 |
| 5 | Alembic 초기 리비전 + ORM 모델 | `alembic upgrade head` → **테이블 6개**(connections·virtual_machines·resource_identities·users·user_connection_scopes·audit_events), 제약 4개, `pg_trgm` 확장 생성 확인 |
| 6 | `fakes/fake_reader.py` + 저장소 upsert (§7.3) | **2회 수집 → VM 1건**. Tools를 끈 2회차에도 **게스트 OS가 남아 있음** (§7.4) |
| 7 | vCenter 어댑터 `errors.py` → `session.py` → `collector.py` | `maxObjects=2` 목 테스트로 **토큰 반복** 확인 |
| 8 | `mapper.py` (VM + 게스트 정보) | Tools 4가지 상태 분기, OS 출처 판정 |
| 9 | `reader.py` + `check_connection` | 4단계 판정 결과 반환, 잘못된 자격증명 → `AuthenticationError` |
| 10 | `application/` 3종 | 수집 유스케이스가 Protocol만 참조 (arch_check 특화 규칙 1) |
| 10b | **인증·계정** — `password.py` → `tokens.py` → `AuthService` → `RegistrationService` → `UserAdminService` | 계획 09 §9 완료 기준. **상태 사유가 비밀번호 검증 후에만 노출**, 마지막 관리자 보호 |
| 10c | **부트스트랩 관리자** (계획 09 §7) | 계정 0명일 때만 생성, 비밀번호 미로깅 |
| 10d | **`AccessScope` 주입** — 저장소 조회 시그니처 + SQL 바인딩 | 범위 밖 연결의 VM이 **목록에서 보이지 않음** (`EXPLAIN`으로 SQL 적용 확인) |
| 11 | `api/` 라우터 — 인증·사용자·연결·자원 | 응답 JSON에 `password`·`password_hash` 키 부재. **`PagedResponse`·중첩 `guest` 형태 일치** (§9.2). 공개 경로 화이트리스트 동작 |
| 12 | `static/` 5화면 (**A6 완료 후**) | 로그인 → 수집 → 목록. 가입 → 승인 → 범위별 조회. 토큰이 `tokens.css`와 일치 |
| 13 | **실제 vCenter 연결** | → Step 2 |

**12번은 A6(핸드오프)에 의존한다.** 디자인이 늦어지면 여기서 대기가 발생하므로,
A1~A2를 백엔드 1번과 같은 날 시작한다.

## 13. Step 1 완료 기준

**디자인 (1-A)**

- [ ] 자원 목록·연결 관리·**로그인·가입·사용자 관리** 시안이 **검토자 승인**을 받음
- [ ] 시안 어디에도 **전원·삭제·스냅샷·마이그레이션 조작 요소가 없음** (FR-1206)
- [ ] `docs/03_design_system.md`에 **핸드오프 번들의 실제 토큰 값**이 기록됨
- [ ] 목 데이터에 실제 서버명·IP가 없음 (NFR-206)
- [ ] `static/css/tokens.css`가 디자인 시스템 문서 §1과 **일치**

**백엔드·UI (1-B·1-C)**

- [ ] `python scripts/arch_check.py --ci` 통과
- [ ] 목 리더로 **2회 수집 후 VM 레코드가 1건** (중복 없음 — FR-303)
- [ ] `resource_identities`에 **VM 수만큼 `rule=1` 행**이 있고, upsert가 `ON CONFLICT`가 아닌
      **식별키 조회 경로**로 갱신함 (§7.3)
- [ ] Tools가 동작하던 VM을 **Tools 미실행 상태로 재수집해도 게스트 OS·호스트명이 유지**되고
      `guest_observed_at`이 **이전 시각 그대로**임 (§7.4)
- [ ] VM 목록 응답이 `PagedResponse{items,total,offset,limit}` 형태이고, 게스트 정보가
      **중첩 객체**로 내려오며 `unavailable_reason`을 **서버가** 채움 (§9.2)
- [ ] 허용 목록에 없는 `sort_by` 값에 **400 응답** (인젝션 차단)
- [ ] `alembic upgrade head`가 **`pg_trgm` 확장 생성까지 성공** (권한 확인)
- [ ] `maxObjects`를 초과하는 VM이 **전량 수집**됨 (페이징 토큰 반복)
- [ ] `missingSet` 속성이 `KeyError` 없이 `None` 처리됨
- [ ] Tools 미설치 VM이 목록에 **`수집 불가 — VMware Tools 미설치`**로 표시됨
- [ ] OS가 구성값에서 온 경우 **`(구성값)`이 병기**됨
- [ ] DB의 `password_encrypted`가 **평문이 아님**
- [ ] API 응답·로그·예외 메시지에 **비밀번호가 없음** (`grep`으로 확인)
- [ ] 잘못된 비밀번호로 연결 테스트 시 **재시도 없이 1회만 시도** (FR-114, 계정 잠금 방지)
- [ ] 수집 실패 시에도 **vCenter 세션이 해제**됨
- [ ] 어댑터 코드에 **쓰기 API 호출 없음** — `grep -rE "_Task\(|SetField" src/infrastructure/vcenter/`
- [ ] 브라우저에서 등록 → 수집 → 목록 조회가 **처음부터 끝까지 동작**

**인증·계정 (D-014)**

- [ ] 로그인하지 않고 `/api/v1/virtual-machines`를 호출하면 **401**
- [ ] `pending` 계정이 **비밀번호가 맞아도 로그인하지 못함**
- [ ] 틀린 비밀번호로는 계정 존재 여부·상태를 **알 수 없음** (응답 문구·시간 모두)
- [ ] 가입 폼이 **아이디 중복을 노출하지 않음** (중복이어도 202 + 동일 문구)
- [ ] 승인 시 부여한 **역할과 조회 범위가 즉시 적용**됨 (재로그인 불필요)
- [ ] 범위를 비운 채 승인한 계정에게 **VM이 하나도 보이지 않음** (기본 거부)
- [ ] 범위 밖 `connection_id`로 조회 시 **403이 아니라 빈 결과**
- [ ] `viewer`가 `/api/v1/connections`·`/api/v1/users`를 호출하면 **403**
- [ ] **마지막 활성 관리자**를 강등·비활성화할 수 없음
- [ ] 세션 쿠키가 `httpOnly`이고 **`document.cookie`로 토큰을 읽을 수 없음**
- [ ] 로그인 성공·실패, 가입 신청, 승인·거부, 역할 변경이 **`audit_events`에 기록**됨
- [ ] 계정 물리 삭제 엔드포인트가 **존재하지 않음**
- [ ] `permissions[]`에 없는 메뉴가 사이드바에 **표시되지 않음** (FR-1213)

### 13.1 검증 결과 (2026-08-07)

`pytest` 71건 통과, `arch_check --ci` 위반 0건. 검증 근거는 아래와 같다.

| 기준 | 근거 |
|---|---|
| 2회 수집 → VM 1건 | `test_collect.py::test_two_collections_produce_one_record` |
| `resource_identities` rule=1 행, 식별키 조회 경로 | `test_identity_rows_written_for_each_vm` (재수집해도 행이 늘지 않음) |
| 게스트 폴백 + `guest_observed_at` 유지 | `test_guest_values_survive_tools_going_down` · `test_guest_info.py` |
| `PagedResponse` 형태 + 중첩 `guest` + 서버가 채운 `unavailable_reason` | `test_api.py::test_vm_list_uses_paged_response_and_nested_guest` |
| 허용 목록 밖 `sort_by` → 400대 | `test_unknown_sort_column_returns_422` (422 — 계획 08 §3.2의 `ValidationError` 매핑) |
| `alembic upgrade head` + `pg_trgm` | `tests/conftest.py`가 매 실행마다 Alembic으로 테스트 DB를 만든다 |
| `maxObjects` 초과 전량 수집 (토큰 반복) | `test_vcenter_collector.py::test_all_pages_are_retrieved` |
| `missingSet` → `None` | `test_missing_set_becomes_none_not_keyerror` |
| Tools 미설치 표시 · OS `(구성값)` 병기 | 브라우저 확인 — `legacy-erp-01`에 `수집 불가 — 게스트 도구 미설치` + `마지막 확인: … (4일 전)`, `batch-prd-01`에 `(구성값)` |
| `password_encrypted`가 평문 아님 | `test_password_is_encrypted_at_rest` (`1$…` 형식) |
| 응답·로그·예외에 비밀번호 없음 | `test_security.py` 마스킹 5종 + `test_audit_records_login_and_connection_create` |
| 인증 실패 시 재시도 없음 | `test_auth_failure_is_not_retried_and_flags_connection` (`start_calls == 1`) |
| 실패 시 세션 해제 | `test_session_is_closed_even_on_failure` · `test_view_is_released_even_on_error` |
| 어댑터에 쓰기 API 호출 없음 | `grep -rE "_Task\(\|SetField\|CustomFieldDef" src/infrastructure/vcenter/` → 0건 (주석 제외) |
| 브라우저 관통 | 로그인 → 연결 목록 → VM 목록 → 사용자 관리 확인, 콘솔 오류 0건 |
| 인증·계정 기준 14항목 | `test_api.py` 26건 (계정 열거 방지, 기본 거부, 마지막 관리자 보호, 즉시 권한 회수 등) |
| 쿠키 `httpOnly` · JS 접근 불가 | 브라우저에서 `document.cookie` 빈 문자열, `localStorage`/`sessionStorage` 비어 있음 |

**미확인 2건** — 실환경이 필요하므로 Step 2로 넘긴다.

| 기준 | 사유 |
|---|---|
| 실제 vCenter 등록 → 수집 → 목록 | 대상 인스턴스 미지정 (§25-4). 현재 검증은 목 커넥터 기준이다 |
| Read-Only 역할로 속성 전량 조회 가능 여부 | §15.1-3·§15.2-7 |

> **구현 중 내린 결정 3건**: D-015(bcrypt 직접 사용), D-016(계층 규칙에 맞춘 모듈 위치 조정),
> D-017(쓰기 트랜잭션 커밋 지점).

---

# Step 2. 실환경 적용·실측

> **이 단계에는 새 기능 구현이 없다.** 계획서 전반의 `[검증 필요]`를 실제 vCenter로 닫는다.

## 14. 왜 별도 단계인가

계획서의 상당수 항목이 **2차 자료 기반 가정**이다 (`docs/00_research_notes.md` §11).
이 가정들은 Step 3 이후 설계의 전제이므로, 더 많이 만들기 전에 확인하는 편이 싸다.

특히 **성능 관련 가정(관리 규모, 수집 소요 시간)은 실측 없이는 인덱스·배치 크기를 정할 수 없다**
(`plans/README.md` §5). Step 1의 얇은 파이프라인은 이 측정에 이상적인 도구다.

## 15. 검증 항목

### 15.1 접속·권한

| # | 확인할 것 | 실패 시 영향 |
|---|---|---|
| 1 | **대상 vCenter 실제 버전 분포** | 6.5 미만이 있으면 CST-10 재검토. 속성 경로 전면 재확인 |
| 2 | **pyVmomi 버전 호환** (조사 §11-9) | 최신 pyVmomi가 6.5에 붙지 않으면 버전 하향 고정 필요 |
| 3 | Read-Only 역할로 VM 조회 가능 여부 | 권한 부족 시 운영팀과 역할 조정 |
| 4 | `customValue`·`customFieldsManager.field` 조회 가능 여부 (조사 §11-10) | FR-606 잔여 범위 판정 |
| 5 | TLS 인증서 상태 (자체 서명 여부) | `verify_tls` 기본값 판단 |
| 6 | 포탈 서버 → vCenter 443 방화벽 (CST-07) | 연결 자체 불가 |

### 15.2 데이터

| # | 확인할 것 | 반영처 |
|---|---|---|
| 7 | §7.2 속성 경로가 **전부 존재하는지** (`missingSet`에 무엇이 오는지) | 계획 04 §5 확정 |
| 8 | **VM 총 건수** | NFR-104 확정 → 인덱스·배치 크기 |
| 9 | **Tools 미설치·미실행 VM 비율** | FR-504 가치 판단, UI 표시 빈도 |
| 10 | 한글 VM 이름·주석의 인코딩 | 매퍼·DB 인코딩 |
| 11 | `instanceUuid`가 **모든 VM에 존재**하는지 | 없으면 CI 식별 2순위(BIOS UUID) 사용 빈도 상승 |
| 12 | 동일 VM이 여러 vCenter에 보이는지 (Linked Mode) | FR-308 교차 매칭 우선순위 |
| 12b | **실제 VM 이름의 최대 길이·명명 규칙** | Step 1 시안은 목 데이터 기준이다. 실제 이름이 더 길면 컬럼 폭·말줄임 규칙 조정 (계획 11 §6.1) |

### 15.3 성능

| # | 측정 | 반영처 |
|---|---|---|
| 13 | **전량 수집 소요 시간** (`config.hardware.device` 제외 상태) | NFR-105 수집 주기 결정 |
| 14 | `maxObjects` 값별 응답 시간 (200 / 500 / 1000) | 계획 04 `DEFAULT_PAGE_SIZE` 확정 |
| 15 | 수집 중 vCenter 부하 (운영팀 관찰) | 동시 연결 수 상한 |
| 16 | VM 목록 API 응답 시간 | NFR-101(1초) 달성 여부 |

### 15.4 포탈 측 DB — vCenter만 보다 놓치는 곳

Step 1을 로컬 컨테이너로 검증했다면 **실제 배포 대상 DB에서는 아직 아무것도 확인되지 않았다.**

| # | 확인할 것 | 실패 시 영향 |
|---|---|---|
| 17 | **`CREATE EXTENSION pg_trgm` 실행 권한** | 없으면 Step 4 통합 검색(FR-403) 설계 변경. **권한 승인은 구현보다 오래 걸린다** |
| 18 | DB 계정의 DDL 권한 (Alembic 실행 가능 여부) | 마이그레이션을 DBA가 대행해야 하면 Step 3 이후 배포 절차가 달라진다 |

## 16. 산출물

1. **`docs/04_field_validation.md` 신규 작성** — 위 18개 항목의 실측 결과와 측정 조건(일시·대상·건수)
2. `docs/00_research_notes.md` §11 갱신 — 해소된 항목 표시
3. `spec.md` 갱신 — NFR-104(규모)·NFR-105(수집 주기)의 `[TODO]` 해소, CST-10 버전 확정
4. `plans/04` §5 속성 목록 확정 (`[검증 필요]` 제거)
5. 필요 시 `docs/02_decision.md`에 결정 추가
6. **디자인 조정 필요 항목 정리** — 실제 이름 길이·Tools 미설치 비율이 시안 가정과 다르면
   `docs/03_design_system.md` §5 결정 이력에 기록하고 캔버스에서 수정한다.
   토큰을 바꾸는 것이 아니라 **컬럼 폭·말줄임·배지 밀도**를 맞추는 수준의 조정이다

## 17. Step 2 완료 기준

- [ ] 실제 vCenter 1개에서 **VM 목록이 브라우저에 표시됨**
- [ ] 수집 2회 실행 후 **VM 건수가 vCenter 실제 건수와 일치**하고 중복이 없음
- [ ] `docs/04_field_validation.md`에 18개 항목 결과가 기록됨
- [ ] `spec.md`의 NFR-104·NFR-105 `[TODO]`가 실측값으로 확정됨
- [ ] 수집 중 vCenter에 **부하 이슈가 없었음**을 운영팀이 확인
- [ ] 실데이터로 본 화면이 시안과 크게 어긋나지 않음 (어긋나면 §16-6으로 조정 기록)

---

# Step 3 이후 — 개요

각 단계는 **앞 단계가 실환경에서 동작한 뒤 착수**한다.
상세 설계는 기존 계획서에 있으므로 여기서는 범위와 완료 기준만 정의한다.

## 18. Step 3. 다중 연결 + 자동 수집

| 항목 | 내용 |
|---|---|
| **목표** | vCenter 여러 대를 등록해 두면 주기적으로 알아서 수집되고, 일부가 실패해도 나머지는 정상 동작 |
| **구현** | `src/orchestration/` (APScheduler), `collection_runs` 테이블, 부분 실패 판정, 신선도 표시, 미발견 유예(FR-307), 연결 수정 API(FR-107·108·110·111), 연결 삭제 2단계 확인(FR-109) |
| **Protocol 확장** | `ReaderCapabilities`, `get_outcomes()` 도입 (계획 03 §3·§5) |
| **참조** | 계획 06 Part B 전체, 08 §5.5·5.6, 11 §14.3 |
| **완료 기준** | 연결 3개 중 1개를 의도적으로 장애 상태로 만들었을 때 **나머지 2개의 수집과 조회가 정상**이고, 실패한 연결의 데이터가 **삭제되지 않고 신선도 경고와 함께 유지**됨 |

## 19. Step 4. VM 상세 속성 + 검색

| 항목 | 내용 |
|---|---|
| **목표** | IP로 VM을 찾을 수 있다 (FR-404 — 최다 사용 시나리오) |
| **구현** | `config.hardware.device` 수집 추가, `vm_disks`·`vm_adapters`·`vm_adapter_ips` 테이블, 스냅샷 수집, 통합 검색·IP 역조회, VM 상세 화면 |
| **참조** | 계획 04 §6.3, 06 §2.4·2.9, 07 §4, 11 §15·16 |
| **주의** | `config.hardware.device` 추가로 **수집 시간이 늘어난다.** Step 2에서 측정한 값과 비교해 증가분을 기록한다 |
| **완료 기준** | IP 주소로 검색하여 **1초 이내**에 해당 VM에 도달 (NFR-101). 링크로컬·루프백이 결과에 없음 |

## 20. Step 5. Hyper-V 어댑터

| 항목 | 내용 |
|---|---|
| **목표** | vCenter와 Hyper-V 자원이 **하나의 표에 동일 컬럼으로** 표시됨 (FR-1203) |
| **구현** | 계획 05 전체(공통 계층 + 수집 경로 2종), 계약 테스트를 세 어댑터 경로에 파라미터화 적용 |
| **선행** | **SCVMM 구축 완료**(CST-09 확정 — D-012), SCVMM `Read-Only Administrator` 계정과 `Remote Management Users` 등록, CST-06(WinRM 인증 방식) — **환경 준비가 구현보다 오래 걸릴 수 있다** |
| **완료 기준** | 계약 테스트 스위트가 **vCenter·Hyper-V 관리자·SCVMM에서 동일하게 통과**. 유스케이스 코드에 하이퍼바이저 분기 없음 (arch_check). 같은 VM을 두 경로로 수집했을 때 `native_id` 일치 (계획 05 §8.4) |
| **순서 조정** | 조직에 Hyper-V 자원이 많다면 **Step 3 직후로 앞당긴다.** 어댑터는 Protocol만 맞추면 독립적이라 순서 제약이 약하다 |

### 20.1 SCVMM이 주 경로다 — 경로 A는 조건부

**SCVMM 도입이 확정되었다** (2026-08-07, D-012 · CST-09 해소). Step 5는 **경로 B만으로 시작한다.**

| 단계 | 내용 |
|---|---|
| 5-1 | 공통 계층 (`session`·`runner`·`errors`·`normalize`) |
| 5-2 | **경로 B (SCVMM)** — 계획 05 §7·§8.4~8.6·§10·§12 |
| 5-3 | SCVMM 미관리 호스트 **실사** — 없으면 Step 5 종료 |
| 5-4 | (남아 있을 때만) **경로 A + JEA** — 계획 05 §4.3·§6·§9 |

**5-3을 건너뛰고 5-4를 만들지 않는다.** 독립 호스트가 실제로 있는지 확인한 뒤 착수한다.
확인 결과 편입 가능한 호스트라면 SCVMM에 등록하는 편이 JEA 구성보다 싸다 (계획 05 §4.3.2).

> **SCVMM 구축 완료가 Step 5의 선행 조건이다.** 구축이 지연되면 Step 5를 뒤로 미루고
> 다른 Step(6·7·8)을 먼저 진행한다. **호스트를 관리 권한 계정으로 임시 수집하는 우회는 하지 않는다** (NFR-201).

## 21. Step 6. Host/Cluster/Datastore/Network

| 항목 | 내용 |
|---|---|
| **목표** | VM 외 자원 조회와 관계 탐색 (호스트 → 그 위의 VM 목록) |
| **구현** | 나머지 `list_*` Protocol 메서드, 자원별 테이블, MoRef → 이름 해석(계획 04 §6.4), 관계 탐색 API |
| **참조** | 계획 04 §6.4, 06 §2.5·§5, 07 §5 |
| **완료 기준** | VM 목록의 호스트 컬럼이 **MoRef가 아닌 실제 호스트명**으로 표시됨. 데이터스토어 용량 오버커밋이 계산됨 |

## 22. Step 7. 메타데이터 + 변경 이력 + 데이터 품질

| 항목 | 내용 |
|---|---|
| **목표** | CMDB로서의 가치 — 소유자 정보와 변경 추적 |
| **구현** | 계획 07 §6(메타데이터), 12 전체(변경 이력·수명주기·데이터 품질), 메타데이터·품질 화면 |
| **권한** | 메타데이터 편집은 `operator` 이상이다. Step 1의 역할 체계를 그대로 쓴다 |
| **완료 기준** | 소유자를 입력한 VM을 **재수집해도 값이 보존**됨 (FR-602). vCPU 변경이 이력에 남고, 변경 이력 조회에 **`AccessScope`가 적용**됨 |

## 23. Step 8. 리포트·대시보드 + 외부 연동

| 항목 | 내용 |
|---|---|
| **목표** | 리포트 산출과 외부 시스템 연동 |
| **구현** | 계획 13 전체(리포트·Excel/CSV 내보내기), 대시보드, 계획 08 §7(외부 API + API 키), **감사 로그 조회 화면**, 외부 인증(FR-1005) 판단 |
| **주의** | **대시보드 집계에도 범위가 적용되어야 한다.** 전체 VM 수를 보여주면 범위 밖 정보가 누설된다 (계획 09 §10) |
| **완료 기준** | Excel 내보내기가 감사 로그에 기록됨 (NFR-206). API 키가 해시로 저장되고 발급 시 1회만 노출됨. 관리자가 감사 로그를 화면에서 조회 |

## 23.1 Step 9. 폴스타 연동 — 게스트 관점 보강

| 항목 | 내용 |
|---|---|
| **목표** | 하이퍼바이저가 알 수 없는 게스트 내부 사실(실제 OS·커널·시리얼·에이전트)을 폴스타에서 가져와 VM에 붙인다. **게스트 도구 미설치 VM의 "수집 불가"를 일부 해소한다** |
| **구현** | 계획 14 전체 — `ServerFactReader` Protocol, DBHub MCP 클라이언트, 폴스타 어댑터, 스냅샷 4테이블, 매칭 엔진, VM 상세 보강 섹션 |
| **선행** | **Step 4 완료** (MAC·IP 수집으로 매칭 규칙 2·4가 성립). 9-B(메타데이터 제안)는 **Step 7 완료**가 추가 선행 |
| **범위 밖** | 갭 분석 화면, 물리 서버 확장, **폴스타 성능 지표·알람 수집**(`spec.md` §1.2 비목표) |
| **완료 기준** | MCP 서버를 중지해도 **VM 조회가 정상**이고 링크·스냅샷이 유지됨. 자동 확정은 BIOS UUID·MAC의 1:1 매칭만. 표본 20건 수동 검증에서 **오매칭 0건** |
| **주의** | 호스트명·IP 매칭을 자동 확정으로 바꾸자는 요구가 반복해서 들어온다. 오매칭은 사람이 발견하기 전까지 정답처럼 보인다 (계획 14 §6.1) |

> **이 단계는 `spec.md`에 근거 요건이 없다.** FR-11xx는 포탈이 데이터를 **제공하는** 아웃바운드
> API이고, 외부 시스템에서 **가져오는** 인바운드 연동 요건은 정의되어 있지 않다.
> 착수 전 spec 개정 여부를 확정한다 (D-019 미해결 항목).

---

## 24. 단계 ↔ 기존 계획서 매핑

| 계획서 | Step 1 | Step 3 | Step 4 | Step 5 | Step 6 | Step 7 | Step 8 |
|---|---|---|---|---|---|---|---|
| 01 프로젝트 구조 | 축소 | 보완 | | | | | |
| 02 도메인 모델 | §3·4 전체, §5.1·5.2(Cpu·Memory·Platform), §6.2, **§7 1순위**, §9.1·9.3, §10 | §7.2 교차 매칭 | §5.2 디스크·NIC·스냅샷, **§7 3순위(MAC)** | §9.1·9.2 Hyper-V 값, **§7 2순위(BIOS)** | §6.3 나머지 엔티티 | | §11 메타데이터 |
| 03 Protocol | §2 축소, §4, §8 | §3 capability, §5 | | §9 계약 테스트 | 나머지 `list_*` | | |
| 04 vCenter 어댑터 | §3·4·5(축소)·6.1·6.2·8 | | §6.3·6.5 | | §6.4 | | §5.2 Custom Attr |
| 05 Hyper-V 어댑터 | | | | **전체** | | | |
| 06 저장소·스케줄러 | §2 축소(**§2.1·2.7 포함**), §3.1~3.3 | **Part B 전체**, §4, §3.1 중복 후보 | §2.4·2.9 | | §2.5·§5 | | §3.4 메타데이터 분리 |
| 07 조회·메타데이터 | **§2 Page·PagedResult**, §3 축소 | | §4 검색 | | §5 관계 | | §6 메타데이터 |
| 08 API | **§3.1·4.1·4.2**, §4 일부, §5.3·5.4·5.7, **§6.3** | §5.5·5.6 | §6.2 | | §6.1 | | §7 외부 API·API 키 |
| 09 인증·RBAC | **§2~4.6·§5(api_keys 제외)·§6·§7** | | | | | | §4.4 외부 인증, API 키 |
| 10 보안·감사 | **§3 전체 + §6 기록** | | | | | §6.2 detail 확장 | §6.5 내보내기 감사, 조회 화면 |
| 11 웹 UI | **Part A(§3~9) + Part B 5화면(§13.2·13.3 포함)** | §14.3 신선도 | §15·16 + 상세 시안 | §14.1 통합 표시 | | §19 품질 화면 | §18 대시보드 + 감사 조회 |
| 12 변경 이력 | | | | | | **전체** | |
| 13 리포트 | | | | | | | **전체** |

**계획 14(폴스타 연동)는 Step 9 전용이다.** 위 표에 열을 두지 않은 이유는 기존 계획서 13종과
달리 어느 Step에도 분할 배치되지 않고 Step 9에서 통째로 구현되기 때문이다 (§23.1).

## 25. 결정이 필요한 항목

| # | 항목 | 기본 제안 | 영향 |
|---|---|---|---|
| 1 | **Claude Design 플랜 보유·활성화 여부** (§10.1) | — | **Step 1 착수 전 필수.** 불가 시 계획 11 §10 대체 경로 |
| 2 | **시안 검토자와 검토 라운드 횟수** (`spec.md` §3.12.1 `[TODO]`) | 운영팀 담당자 1명, 2라운드 | 미지정 시 승인 없이 확정되어 Step 3 이후 재작업 |
| 3 | Hyper-V 어댑터를 Step 3 직후로 앞당길지 | 현행 Step 5 유지 | 조직의 Hyper-V 자원 비중에 따름. **SCVMM 구축 일정이 상한을 정한다** (§20.1) |
| 4 | Step 1 검증에 사용할 vCenter 인스턴스 | — | **Step 2 착수 전 필수.** 운영 영향이 적은 것 우선 |
| 5 | Step 1 배포 환경 (로컬 / 개발 서버) | 로컬 + PostgreSQL 컨테이너 | CST-08 `[TODO]`와 연동 |
| 6 | **실배포 대상 DB의 확장 설치·DDL 권한 확보 주체** (§15.4) | 포탈 전용 DB + 소유자 권한 | 권한이 없으면 Step 4 검색 설계가 바뀐다. **승인 신청은 Step 1 착수와 동시에** |

**1·2번은 Step 1 첫날에 필요하다.** 디자인 트랙(1-A)이 백엔드와 병렬로 시작하기 때문이다.
**6번도 첫날에 신청한다.** 확인은 Step 2지만 승인 대기가 길면 그때는 이미 늦다.

## 26. 리스크

| 리스크 | 징후 | 대응 |
|---|---|---|
| **실환경 접근이 지연되어 Step 2가 막힘** | vCenter 계정·방화벽 승인 대기 | Step 1 착수와 **동시에** 읽기 전용 계정과 방화벽을 신청한다. 승인은 구현보다 오래 걸릴 수 있다 |
| Step 1 범위가 슬금슬금 늘어남 | "이왕 하는 김에 IP도" | §5.2 범위 밖 목록을 기준으로 판단. 추가 요구는 Step 번호를 붙여 뒤로 넘긴다 |
| **Claude Design을 쓸 수 없어 1-C가 막힘** | 플랜 미보유, Enterprise 관리자 승인 지연 | §10.1을 Step 1 **첫날에** 확인한다. 불가 시 즉시 계획 11 §10 대체 경로로 전환하고 §10.2·§10.5는 그대로 수행 |
| **시안에 조작 버튼이 들어옴** | "관리 화면이니까" 전원·삭제 버튼 제안 | 계획 11 §6.1 마지막 항목을 **화면마다** 확인. 대화를 이어가면 다시 들어온다 |
| 시안이 Step 1 데이터만 보고 만들어짐 | 목 데이터가 8행짜리 이상적 표 | §10.2대로 계획 11 §5.2 경계 사례를 **전부** 투입. 시안은 넓게, 구현은 좁게 |
| 실측 결과가 계획과 크게 다름 | 속성 경로 다수 누락, 수집 시간 과다 | **정상적인 결과다.** Step 2가 존재하는 이유이며, Step 3 이후 설계를 그때 조정한다 |
| **범위 필터가 새 조회 경로에서 누락됨** | Step 4 검색, Step 7 이력, Step 8 대시보드 집계를 추가할 때 | 계획 09 §10이 지목한 **이 계획의 가장 흔한 결함**이다. 새 조회 경로마다 `scope` 인자를 확인하고, 범위 없는 전체 조회 함수를 만들지 않는다 |
| **관리자 계정이 0명이 되어 잠김** | 마지막 관리자를 강등·비활성화 | 계획 09 §4.6.1의 가드로 차단. 그래도 발생하면 DB 직접 수정 외에 방법이 없다 |
| 검증 전에 사내 전체에 공개됨 | Step 2 완료 전 "잠깐만 보여달라" 요청 | 인증이 있어도 데이터 정확성이 확인되지 않았다. Step 1~2는 관리자 계정 1~2개로 운영하고 가입 승인을 시작하지 않는다 (§3) |
