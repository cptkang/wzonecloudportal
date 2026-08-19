# 구현 계획: Step 1 구현 개선

> 작성일 2026-08-19 · 대상: 이미 구현된 Step 1 (2026-08-07 완료, 2026-08-14 vcsim 관통 검증)
> 근거 문서: `plans/ROADMAP.md` §5~§13, `plans/04-vcenter-adapter.md`, `docs/02_decision.md` D-020

> **진행 상황 (2026-08-19)**: **Phase 0~2 + T8 완료.** 상세는 §실행 결과.
> Phase 4는 C3(Step 2 실측) 대기, Phase 5는 사용자 승인 대기.

## Overview

Step 1은 완료 기준 대부분을 충족했고 `pytest` 71건·`arch_check --ci`가 통과한다.
이 계획은 **새 기능을 추가하지 않는다.** 이미 만든 Step 1 범위 안에서 발견된
**요건 대비 누락 3건, 개발 루프 결함 2건, 상한 대응 2건, 성능 부채 3건**을 닫는다.

조사는 `src/`(≈5,800줄)·`tests/`(≈2,900줄)·`static/`(≈3,600줄) 전체를 읽고
로컬에서 `pytest tests/unit`·`ruff`·`arch_check`를 실행해 확인했다.

**Step 2(실환경 실측)와의 관계가 이 계획의 핵심 제약이다.** ROADMAP §14는
*"Step 1의 얇은 파이프라인은 이 측정에 이상적인 도구다"* 라고 명시한다.
따라서 **성능 최적화는 실측 전에 하지 않는다** — 최적화 후 측정하면 무엇이
병목이었는지 영원히 알 수 없고, 인덱스·배치 크기·수집 주기(NFR-104·105)를
정할 근거를 잃는다. 대신 **실측의 해상도를 높이는 계측만 먼저 넣는다.**

## Architecture Decisions

### AD-1. 성능 최적화는 Step 2 실측 이후로 미룬다

- upsert N+1(VM당 4~5 쿼리), `_sync_identities` 무조건 DELETE+INSERT,
  `RetrieveContent()` 페이지당 재호출 — 세 가지 모두 실재하는 부채다.
- 그러나 지금 고치면 ROADMAP §15.3-13(전량 수집 소요 시간)의 측정 대상이 바뀐다.
- **Phase 3에 계측만 넣고, 최적화는 체크포인트 C3(실측 완료) 뒤로 배치한다.**
- 예외: 계측 자체가 병목을 드러내지 못하는 구조라면 계측 설계가 잘못된 것이므로,
  T8에서 "어댑터 조회 시간"과 "DB 반영 시간"을 반드시 분리해 기록한다.

### AD-2. 기능 갭은 수직 슬라이스로 닫는다

`configured_os`는 vCenter 매퍼 → 도메인 → DB → `VmSummary`까지 흘러오다가
**`VmSummaryResponse`에서 끊긴다** (`src/api/schemas/inventory.py:39`).
스키마만 고치면 UI가 그 값을 쓰지 않아 화면은 그대로다. **매퍼·응답·화면·테스트를
한 태스크로 묶어** 한 번에 관통시킨다 (T3).

### AD-3. 개발 루프 복구를 Phase 0에 둔다

현재 `pytest tests/unit`이 **PostgreSQL 없이는 105건 전부 에러**다
(`tests/conftest.py:46`의 `_prepare_database`와 `:101`의 `_clean_tables`가 둘 다
`autouse=True`). 순수 매퍼 테스트조차 DB에 붙는다. 또 `ruff`·`mypy`가 **설치조차
되어 있지 않고** CI 워크플로가 없다. 이 상태로 T3 이후를 진행하면 회귀를 잡을
수단이 없으므로 **Phase 0을 먼저 끝낸다.**

### AD-4. Step 1 범위를 넓히지 않는다

발견 사항 중 **Step 1 범위 밖은 이 계획에서 다루지 않는다**:
IP·디스크·스냅샷(Step 4), Host/Cluster/Datastore(Step 6), Custom Attributes(Step 8),
NSX opaque 백킹 분기(계획 04 §6.3 — Step 1엔 NIC 매핑 자체가 없다).
D-020의 VCF 9 대응 중 **지금 코드로 닫을 수 있는 2건만** Phase 2에 넣는다.

## 의존성 그래프

```
Phase 0 (개발 루프)
  T1 conftest 분리 ──┐
  T2 린트·타입·CI ───┴──► 이후 모든 태스크의 검증 수단
                              │
Phase 1 (기능 갭)             ▼
  T3 구성값 OS 관통 (매퍼→응답→UI→테스트)   ← 최우선, 독립
  T4 수집 실패 가시화 ──► T5 중복 수집 차단
                              │
Phase 2 (상한 대응 · D-020)   ▼
  T6 pyvmomi 핀           (독립)
  T7 TLS minimum_version  (독립)
                              │
Phase 3 (실측 대비)           ▼
  T8 수집 구간별 계측  ──► [C3: Step 2 실환경 실측]
                              │
Phase 4 (실측 후 최적화)      ▼
  T9  upsert 배치화 ──► T10 identity 조건부 동기화
  T11 RetrieveContent 캐시  (독립)
                              │
Phase 5 (정리 · 선택)         ▼
  T12 api.js 목 코드 분리
  T13 hypervisor 문자열 하드코딩 제거
```

## Task List

### Phase 0: 개발 루프 복구

- [x] T1: 단위 테스트를 DB 없이 실행 가능하게 분리
- [x] T2: 린트·타입 검사 정비와 CI 게이트

### Checkpoint C1: 개발 루프

- [x] `pytest tests/unit`이 **PostgreSQL 없이** 전부 통과 — 110건
- [x] `ruff check` · `mypy src` · `arch_check --ci` 3종이 로컬에서 통과
- [~] CI 워크플로 작성 완료 — **실제 실행 확인은 push 후**
- [ ] 사람 검토 후 다음 Phase 진행

### Phase 1: 요건 대비 기능 갭

- [x] T3: 구성값 OS(`configured_os`)를 응답·화면까지 관통
- [x] T4: 백그라운드 수집 실패를 연결 상태에 기록
- [x] T5: 동일 연결의 수집 중복 실행을 서버에서 차단

### Checkpoint C2: 기능 갭

- [x] Tools 미설치 VM의 화면에 **구성값 OS가 보인다**
- [x] 수집이 실패하면 UI가 **"수집 중…"에 머무르지 않고** 실패 사유를 보여준다
- [x] 수집 버튼 연타에도 백그라운드 작업이 하나만 뜬다
- [x] Step 1 완료 기준(ROADMAP §13) 전 항목 재확인 — 회귀 없음
- [ ] 사람 검토 후 다음 Phase 진행

### Phase 2: 최신 VCF 대응 (D-020 중 코드로 닫히는 것)

- [x] T6: `pyvmomi` 버전 핀
- [x] T7: TLS 하한 명시

### Checkpoint C3: Step 2 실환경 실측

> **이 계획의 태스크가 아니다.** ROADMAP §15의 25개 항목을 실환경에서 확인하는
> Step 2 자체다. Phase 4는 이 결과 없이는 시작하지 않는다.

- [ ] `docs/04_field_validation.md`에 실측 결과 기록 (ROADMAP §16-1)
- [x] **수집 소요 시간의 구간별 분해**가 T8의 로그로 확보됨 (T8 완료)
- [ ] 병목이 어댑터 조회인지 DB 반영인지 판정됨

### Phase 4: 성능 최적화 — **C3 통과 후에만 착수**

- [ ] T9: upsert를 배치 조회 기반으로 전환
- [ ] T10: 식별 행 동기화를 변경분에만 적용
- [ ] T11: `ServiceInstanceContent` 캐시

### Checkpoint C4: 성능

- [ ] 동일 환경·동일 VM 수에서 수집 시간이 **측정값으로** 개선됨
- [ ] 2회 수집 후 VM 1건(FR-303)·식별 행 수 불변 — 회귀 없음
- [ ] 개선 전후 수치를 `docs/04_field_validation.md`에 병기

### Phase 5: 정리 (선택 — 사용자 승인 후)

- [ ] T12: `static/js/api.js`의 목 구현 분리
- [ ] T13: `hypervisor` 판정의 문자열 하드코딩 제거

---

## 태스크 상세

### T1: 단위 테스트를 DB 없이 실행 가능하게 분리

**Description**
`tests/conftest.py`의 `_prepare_database`(session, autouse)와 `_clean_tables`(autouse)가
**모든** 테스트에 걸려 있어, 순수 매퍼·마스킹 테스트까지 PostgreSQL을 요구한다.
현재 DB가 없는 환경에서 `pytest tests/unit`은 105건 전부 `ConnectionRefusedError`다.
DB 픽스처를 `tests/integration/conftest.py`로 내려 단위 테스트를 인프라에서 떼어낸다.

**Acceptance criteria**
- [ ] `pytest tests/unit`이 PostgreSQL 없이 전부 통과한다
- [ ] `pytest tests/integration`은 기존과 동일하게 DB를 준비하고 통과한다
- [ ] 루트 `conftest.py`에는 환경변수 기본값 설정만 남는다 (DB 연결 없음)

**Verification**
- [ ] `docker stop wzoneportal-db` 상태에서 `python -m pytest tests/unit -q` → 통과
- [ ] DB 기동 후 `python -m pytest -q` → 기존 71건 + 이후 추가분 통과
- [ ] `python scripts/arch_check.py --ci` → 위반 0

**Dependencies:** None
**Files likely touched:** `tests/conftest.py`, `tests/integration/conftest.py`(신규)
**Estimated scope:** S (1-2 files)

---

### T2: 린트·타입 검사 정비와 CI 게이트

**Description**
`ruff`·`mypy`가 설치되어 있지 않다(`pip list`로 확인). 게다가 코드에는
`# noqa: BLE001` `SLF001` `ARG001`이 붙어 있는데 **`pyproject.toml`의 ruff 설정에
`lint.select`가 없어 해당 룰이 애초에 꺼져 있다** — noqa가 아무것도 억제하지 않는다.
CI 워크플로(`.github/workflows/`)도 없어 품질 게이트가 전부 수동이다.

**Acceptance criteria**
- [ ] `pyproject.toml`에 `[tool.ruff.lint] select`가 명시되고, 기존 `noqa`가
      가리키는 룰(`BLE`, `SLF`, `ARG`)이 실제로 활성화된다
- [ ] `ruff check src tests` · `mypy src`가 **위반 0**으로 통과한다
      (위반이 나오면 코드를 고치거나 규칙을 명시적으로 조정하고 근거를 남긴다)
- [ ] CI 워크플로가 `ruff` → `mypy` → `arch_check --ci` → `pytest`를 순서대로 실행한다
- [ ] `CLAUDE.md` 개발 환경 절에 dev 의존성 설치와 3종 게이트 실행법이 적혀 있다

**Verification**
- [ ] `pip install -e ".[dev]"` 후 3종 명령이 모두 exit 0
- [ ] CI가 PR에서 실행되고, 일부러 넣은 위반이 CI를 실패시키는지 1회 확인

**Dependencies:** T1 (CI가 단위/통합 테스트를 분리 실행하려면 T1이 먼저)
**Files likely touched:** `pyproject.toml`, `.github/workflows/ci.yml`(신규), `CLAUDE.md`
**Estimated scope:** M (3-5 files)

---

### T3: 구성값 OS(`configured_os`)를 응답·화면까지 관통 — **최우선**

**Description**
ROADMAP §5.3은 게스트 OS 컬럼의 출처를
**"`guest.guestFullName` → 없으면 `config.guestFullName`"** 으로 규정한다.
구성값 OS는 **게스트 도구 없이도 vCenter에서 조회되는 값**이고, 실제로
`virtual_machines.configured_os` 컬럼에 저장되고 있다.

그런데 값이 여기서 끊긴다:

| 지점 | 상태 |
|---|---|
| `vcenter/mapper.py:48` | `PlatformSpec(configured_os=...)` — 채워짐 ✅ |
| `vm_repo.py:287` | DB 컬럼에 저장 ✅ |
| `domain/query.py:81` | `VmSummary.configured_os` 존재 ✅ |
| **`api/schemas/inventory.py:39`** | **`VmSummaryResponse`에 필드 없음** ❌ |
| `static/js/format.js:90` | `osField()`가 `g.os_name`만 본다 ❌ |

결과적으로 **Tools 미설치 VM은 "수집 불가 — 게스트 도구 미설치"만 표시되고,
알 수 있는 구성값 OS가 화면에서 사라진다.** SCVMM 매퍼도 `configured_os`를
채우므로(`scvmm_mapper.py:75`) Hyper-V 경로에서도 동일하게 손실된다.

ROADMAP §5.3이 *"게스트 OS 컬럼이 이 MVP의 시금석"* 이라고 부른 바로 그 지점이다.

**Acceptance criteria**
- [ ] `VmSummaryResponse`가 구성값 OS를 내려보낸다 (게스트 중첩 객체와 별개 필드로 —
      게스트 도구 산출물이 아니므로 `guest` 안에 넣지 않는다)
- [ ] Tools 미설치 VM의 게스트 OS 셀에 **수집 불가 사유와 구성값 OS가 함께** 표시된다
- [ ] 구성값 출처 표시가 유지된다 (FR-304) — 구성값임이 화면에서 구별된다
- [ ] 폴백으로 남은 이전 게스트 값이 있으면 기존 "마지막 확인" 표시가 그대로 동작한다

**Verification**
- [ ] 단위: Tools 미설치 VM의 응답에 구성값 OS가 실린다 (`tests/unit`)
- [ ] 통합: `test_api.py`에 Tools 미설치 VM 응답 계약 검증 추가
- [ ] 수동: vcsim으로 수집 후 브라우저에서 VM 목록 확인 — vcsim은 게스트 도구가 없어
      **모든 VM이 이 경로를 탄다** (CLAUDE.md 개발 환경 절). 회귀 확인에 이상적이다
- [ ] `python scripts/arch_check.py --ci` 통과

**Dependencies:** T1, T2 (검증 수단)
**Files likely touched:** `src/api/schemas/inventory.py`, `static/js/format.js`,
`tests/unit/test_*.py`, `tests/integration/test_api.py`
**Estimated scope:** M (3-5 files)

---

### T4: 백그라운드 수집 실패를 연결 상태에 기록

**Description**
`src/api/routes/connections.py:180`의 `_run_collection`은 예외를 잡아
`session.rollback()` + `logger.exception()`만 한다. **`mark_failure`를 호출하지 않는다.**
`start_collection`이 이미 `mark_attempt`로 `last_attempt_at`만 갱신해 둔 상태이므로,
DB 오류·매핑 예외 등으로 수집이 죽으면 `last_success_at`도 `last_error`도 바뀌지 않는다.

UI(`static/js/connections.js:152`)는 `last_success_at` 변화로 완료를 판정하므로
**"수집 중…"이 영원히 유지된다.** 사용자에게 실패가 전달되지 않는다.

`CollectService`는 자신이 아는 실패(인증·권한·도달 불가)를 이미 `mark_failure`로
기록한다 — 이 태스크는 **그 밖의 예외**를 담당한다.

**Acceptance criteria**
- [ ] `_run_collection`이 잡은 예외에 대해 연결 상태를 실패로 기록한다
- [ ] 기록된 오류 메시지에 **자격증명이 포함되지 않는다** (NFR-203, 마스킹 경유)
- [ ] 실패해도 **기존 수집 데이터는 삭제되지 않는다** (NFR-302)
- [ ] UI가 실패 사유를 표시하고 폴링을 멈춘다

**Verification**
- [ ] 통합: 저장소에서 예외를 던지도록 주입하고, 연결의 `last_error`가 채워지며
      `status`가 실패 상태로 바뀌는지 확인
- [ ] 통합: 그 오류 메시지에 비밀번호 문자열이 없는지 `grep` 방식 검증
- [ ] 수동: 수집 중 DB를 끊고 UI가 "수집 중…"에서 벗어나는지 확인

**Dependencies:** T1, T2
**Files likely touched:** `src/api/routes/connections.py`, `static/js/connections.js`,
`tests/integration/test_collect.py`
**Estimated scope:** S (2-3 files)

---

### T5: 동일 연결의 수집 중복 실행을 서버에서 차단

**Description**
`POST /connections/{id}/collect`는 호출될 때마다 `BackgroundTasks`에 작업을 추가한다.
서버 측 중복 방지가 없다. UI는 버튼을 비활성화하지만(`connections.js:90`)
**다른 탭·다른 관리자·API 직접 호출이면 막히지 않는다.**

동시에 같은 연결을 수집하면 두 트랜잭션이 같은 VM을 insert하려다
`uq_vm_native` 제약에 걸리고, T4로 실패가 기록되더라도 **정상 수집이 실패로 뒤집힌다.**

Step 3에서 Redis 분산 락이 들어오지만(ROADMAP §18), Step 1 상태에서도
**단일 프로세스 범위의 중복은 막을 수 있다.**

**Acceptance criteria**
- [ ] 이미 수집 중인 연결에 수집을 다시 요청하면 새 작업이 생성되지 않는다
- [ ] 두 번째 요청이 **오류가 아니라** "이미 진행 중"임을 알 수 있는 응답을 준다
      (202 유지 + 상태 구분, 또는 409 — 응답 계약은 구현 시 확정하고 UI와 맞춘다)
- [ ] 수집이 끝나면(성공·실패 모두) 다시 수집할 수 있다
- [ ] **Step 3의 분산 락으로 교체될 자리임이 코드 주석에 남는다** — 이 구현은
      단일 프로세스 전제이며, 워커를 여러 개 띄우면 성립하지 않는다

**Verification**
- [ ] 통합: 같은 연결에 연속 2회 요청 → 백그라운드 작업 1회만 실행됨
- [ ] 통합: 수집 완료 후 재요청 → 정상 실행됨
- [ ] 수동: 두 브라우저 탭에서 동시에 [지금 수집] 클릭

**Dependencies:** T4 (실패 경로에서도 진행 표시가 해제되어야 하므로)
**Files likely touched:** `src/api/routes/connections.py`, `static/js/connections.js`,
`tests/integration/test_collect.py`
**Estimated scope:** S (2-3 files)

---

### T6: `pyvmomi` 버전 핀

**Description**
`pyproject.toml:14`의 `pyvmomi`에 버전 제약이 없고, 이 환경에는 **9.1.0.0**이
설치되어 있다. Broadcom의 호환 정책은 **직전 4개 vSphere 릴리스**이므로 지원 하한
6.5는 범위 밖이다. `docs/00_research_notes.md` §11-9가 이미 "버전 고정" 결론을 냈는데
계획서와 `pyproject.toml` 어디에도 반영되지 않았다 (D-020, 계획 04 §14.2).

**Acceptance criteria**
- [ ] `pyproject.toml`의 `pyvmomi`에 하한·상한이 명시된다 (`>=8.0.3,<10`)
- [ ] 계획 04 §14.2의 조치 항목이 완료로 갱신된다
- [ ] 기존 테스트가 그대로 통과한다

**Verification**
- [ ] `pip install -e ".[dev]"` 재실행 후 `python -m pytest` 통과
- [ ] `python -c "import pyVmomi; print(pyVmomi.version_info_str)"`로 설치 버전 확인

**Dependencies:** None
**Files likely touched:** `pyproject.toml`, `plans/04-vcenter-adapter.md`
**Estimated scope:** XS (1-2 files)

---

### T7: TLS 하한 명시

**Description**
`src/infrastructure/vcenter/session.py:45`의 `_build_ssl_context`는
`verify_tls=false`일 때 `check_hostname=False` + `CERT_NONE`만 설정하고
프로토콜 하한을 지정하지 않는다. VCF 9는 TLS 1.3이 기본이고 기본 프로파일이
1.2를 폴백으로 남기지만, 강화 프로파일(`NIST_2024_TLS_13_ONLY`) 사이트도 있다.
현 Python/OpenSSL 조합은 충족하나 **의도를 코드에 고정한다** (계획 04 §3.1).

**Acceptance criteria**
- [ ] `_build_ssl_context`가 `minimum_version`을 명시한다
- [ ] `verify_tls=true`/`false` 양쪽 경로가 기존과 동일하게 동작한다
- [ ] 계획 04 §3.1의 해당 항목이 완료로 갱신된다

**Verification**
- [ ] 단위: 두 경로의 컨텍스트 속성 검증
- [ ] 수동: vcsim(자체 서명, `verify_tls=false`)으로 수집 관통 재확인

**Dependencies:** None
**Files likely touched:** `src/infrastructure/vcenter/session.py`,
`tests/unit/test_vcenter_*.py`, `plans/04-vcenter-adapter.md`
**Estimated scope:** XS (1-2 files)

---

### T8: 수집 구간별 소요 시간 계측 — **Step 2 실측의 정확도를 좌우한다**

**Description**
현재 계측은 어댑터의 `elapsed_ms` 하나뿐이다(`vcenter/reader.py:89`) — 이 값은
**PropertyCollector 조회 시간만** 담는다. DB upsert 시간은 어디에도 기록되지 않는다.

ROADMAP §15.3-13은 "전량 수집 소요 시간"을 측정해 NFR-105(수집 주기)를 정하라고 한다.
그런데 총 시간만 재면 **병목이 vCenter 왕복인지 DB 반영인지 구분할 수 없고**,
Phase 4에서 무엇을 고쳐야 할지 정할 수 없다.

**Acceptance criteria**
- [ ] 수집 1회에 대해 **어댑터 조회 시간**과 **DB 반영 시간**이 분리 기록된다
- [ ] 배치(`collection_batch_size`) 단위 반영 시간과 처리 건수가 기록된다
- [ ] 로그에 **자격증명·VM 이름 등 민감 정보가 포함되지 않는다** (인벤토리 정보 자체가
      민감 — NFR-206). 집계 수치만 남긴다
- [ ] 기존 `CollectSummary` 계약을 깨지 않는다 (UI·테스트 영향 없음)

**Verification**
- [ ] 통합: fake 리더로 수집 후 두 구간이 모두 로그에 남는지 확인
- [ ] 수동: vcsim(VM 20대)으로 수집해 로그 형식 확인
- [ ] 로그 문자열에 비밀번호·개별 VM 이름이 없는지 확인

**Dependencies:** T1, T2
**Files likely touched:** `src/application/collect_service.py`,
`tests/integration/test_collect.py`
**Estimated scope:** S (1-2 files)

---

### T9: upsert를 배치 조회 기반으로 전환 — **C3 이후**

**Description**
`vm_repo.upsert_virtual_machines`(`:80`)는 VM **하나당** 다음을 수행한다.

1. `_find_by_identity` — 식별 규칙 수만큼 SELECT
2. `_load_vm` — `session.get`
3. `_update_vm` — `session.get` + `flush`
4. `_sync_identities` — DELETE + INSERT n건 + `flush`

`collection_batch_size=500`으로 모으지만 **루프 안에서 건별 쿼리를 날린다.**
5,000 VM이면 2만 회 이상의 왕복이다. 배치 단위로 식별키를 `IN` 조회 1회,
기존 행 일괄 로드, 변경분만 UPDATE 하는 구조로 바꾼다.

> **C3(실측) 전에 착수하지 않는다.** 실측 결과 DB 반영이 병목이 아니라면
> 이 태스크는 **하지 않는 것이 옳다** (AD-1).

**Acceptance criteria**
- [ ] 배치당 식별 조회가 **1회**로 줄어든다
- [ ] `_find_by_identity`의 **모호성 감지(`limit(2)`) 동작이 보존된다** — 같은 키를 가진
      자원이 둘이면 임의 선택하지 않고 다음 우선순위로 넘어간다
- [ ] 2·3순위 식별(Step 4·5) 추가 시 **구조 변경 없이 규칙만 늘면 되는** 형태를 유지한다
      (`ON CONFLICT` 기반으로 되돌리지 않는다 — `vm_repo.py:3` 주석의 근거)
- [ ] 게스트 폴백(`_merge_guest`)이 그대로 동작한다

**Verification**
- [ ] 기존 테스트 전부 통과 — 특히 `test_two_collections_produce_one_record`,
      `test_identity_rows_written_for_each_vm`, `test_guest_values_survive_tools_going_down`
- [ ] 쿼리 수 검증 테스트 추가 (배치 크기 N에서 발행 쿼리 수가 N에 비례하지 않음)
- [ ] T8 계측으로 개선 전후 DB 반영 시간 비교

**Dependencies:** C3, T8
**Files likely touched:** `src/infrastructure/repository/vm_repo.py`,
`tests/integration/test_collect.py`
**Estimated scope:** M (2-3 files, 로직 난이도 높음)

---

### T10: 식별 행 동기화를 변경분에만 적용 — **C3 이후**

**Description**
`_sync_identities`(`vm_repo.py:166`)는 **매 수집마다 무조건** 해당 자원의 식별 행을
전부 DELETE하고 다시 INSERT한다. 키가 바뀌지 않았으면 아무것도 할 필요가 없다.
5,000 VM × 매 수집 = 5,000 DELETE + 5,000 INSERT + 인덱스 갱신이 낭비된다.

**Acceptance criteria**
- [ ] 식별 키가 변하지 않은 자원에 대해 DELETE·INSERT가 발생하지 않는다
- [ ] 키가 늘거나 줄면 기존과 동일하게 반영된다 (Step 4·5 대비)
- [ ] `resource_identities` 행 수가 재수집으로 증가하지 않는다 (기존 기준 유지)

**Verification**
- [ ] `test_identity_rows_written_for_each_vm` 통과 (재수집 후 행 수 불변)
- [ ] 키를 바꾼 VM을 재수집하면 행이 갱신되는 테스트 추가
- [ ] T8 계측으로 개선 전후 비교

**Dependencies:** T9
**Files likely touched:** `src/infrastructure/repository/vm_repo.py`,
`tests/integration/test_collect.py`
**Estimated scope:** S (1-2 files)

---

### T11: `ServiceInstanceContent` 캐시 — **C3 이후**

**Description**
`VCenterSession.content`(`session.py:33`)는 접근할 때마다
`self._si.RetrieveContent()`를 호출한다. **이것은 SOAP 왕복이다.**
`collector._retrieve_page_sync`(`collector.py:78`)가 `self._session.content.propertyCollector`를
**페이지마다** 평가하므로 페이지당 왕복이 2배가 된다.
`server_version`(`session.py:42`)도 매번 왕복한다.

**Acceptance criteria**
- [ ] 세션 1개당 `RetrieveContent()` 호출이 **1회**로 줄어든다
- [ ] 세션을 닫으면 캐시가 무효화된다 (닫힌 세션에서 접근 시 기존과 동일하게 `CollectionError`)
- [ ] 페이징·`missingSet`·뷰 해제 동작이 그대로다

**Verification**
- [ ] `test_vcenter_collector.py` 4종 전부 통과
- [ ] 목 세션으로 `RetrieveContent` 호출 횟수 검증 테스트 추가
- [ ] vcsim으로 관통 재확인

**Dependencies:** C3
**Files likely touched:** `src/infrastructure/vcenter/session.py`,
`tests/unit/test_vcenter_collector.py`
**Estimated scope:** XS (1-2 files)

---

### T12: `static/js/api.js`의 목 구현 분리 — 선택

**Description**
`static/js/api.js`는 632줄 중 약 430줄이 **`USE_MOCK = true`일 때만 쓰이는 목 구현**이다
(sessionStorage 기반 가짜 사용자 저장소, 클라이언트 측 `hash()` 등). 현재
`USE_MOCK = false`(`api.js:7`)이므로 동작에는 영향이 없지만, 매 페이지 로드마다
파싱되고 **상수 한 글자를 바꾸면 인증이 클라이언트 측 목으로 대체된다.**

> CLAUDE.md는 "관련 없는 죽은 코드는 언급만 하고 지우지 말라"고 한다.
> 이 항목은 **제안이며 사용자 승인 후에만 진행한다.**

**Acceptance criteria**
- [ ] 목 구현이 별도 파일로 분리되고, 프로덕션 페이지에서 로드되지 않는다
- [ ] 디자인 확인용으로 목을 쓰는 방법이 문서에 남는다
- [ ] 모든 화면이 기존과 동일하게 동작한다

**Verification**
- [ ] 브라우저에서 로그인 → 연결 → VM 목록 → 사용자 관리 관통, 콘솔 오류 0건
- [ ] 목 분리 후에도 목 모드로 화면 확인이 가능한지 1회 확인

**Dependencies:** C2
**Files likely touched:** `static/js/api.js`, `static/js/api.mock.js`(신규), `static/*.html`
**Estimated scope:** M (3-5 files)

---

### T13: `hypervisor` 판정의 문자열 하드코딩 제거 — 선택

**Description**
`vm_repo._row_to_summary`(`:330`)가
`HypervisorKind.VCENTER if kind == "vcenter" else HypervisorKind.HYPERV`로
**리터럴 문자열을 비교**한다. `ConnectionKind` enum 값이 바뀌면 조용히 전부
`HYPERV`로 떨어진다. 또 계획 04 §12에 새로 적은 원칙
*"제품명 문자열이 아니라 `ConnectionKind`로 판별한다"* 와도 결이 맞지 않는다.

**Acceptance criteria**
- [ ] `ConnectionKind` → `HypervisorKind` 매핑이 **enum 기반**으로 한 곳에 정의된다
- [ ] 매핑되지 않은 값이 조용히 잘못된 값으로 떨어지지 않는다
- [ ] SCVMM·hyperv-host·hyperv-cluster가 모두 올바르게 매핑된다

**Verification**
- [ ] 단위: 4개 `ConnectionKind` 전부에 대한 매핑 테스트
- [ ] `python scripts/arch_check.py --ci` 통과

**Dependencies:** C2
**Files likely touched:** `src/domain/enums.py`, `src/infrastructure/repository/vm_repo.py`,
`tests/unit/`
**Estimated scope:** S (2-3 files)

---

## Risks and Mitigations

| 리스크 | 영향 | 완화 |
|---|---|---|
| **성능 최적화를 실측 전에 해버림** | 높음 | Phase 4를 C3 뒤에 두고, C3 없이 착수 금지를 태스크 본문에 명시 (AD-1) |
| T1의 conftest 분리가 기존 통합 테스트를 깨뜨림 | 중간 | DB 픽스처를 **이동만** 하고 내용은 바꾸지 않는다. C1에서 전량 재실행 |
| T3의 응답 계약 변경이 UI와 어긋남 | 중간 | 수직 슬라이스로 응답·화면·테스트를 한 태스크에 묶는다 (AD-2) |
| T5의 중복 차단이 단일 프로세스 전제라 워커 다중화 시 무력화 | 중간 | 한계를 코드 주석과 수락 기준에 명시. Step 3 분산 락으로 교체될 자리임을 남긴다 |
| Step 2 실환경 적용이 지연되어 Phase 4가 무기한 대기 | 중간 | Phase 0~2는 실환경과 무관하게 완결된다. Phase 4만 대기 |
| T2에서 ruff 룰을 켜자 위반이 대량 발생 | 낮음 | 룰을 한 번에 다 켜지 않는다. 기존 `noqa`가 가리키는 룰부터 켜고 확대 |
| T9가 CI 식별 규칙을 미묘하게 바꿔 중복 레코드 발생 | **높음** | 모호성 감지(`limit(2)`) 보존을 수락 기준에 못박고, 기존 식별 테스트 3종을 회귀 기준으로 삼는다 |

## Open Questions

1. **성능 최적화(Phase 4)를 정말 Step 2 실측 뒤로 미룰 것인가?**
   이 계획은 ROADMAP §14를 근거로 "미룬다"를 권고한다. 실환경 적용 일정이 불투명하면
   판단이 달라질 수 있다 — 그 경우 T8(계측)만 먼저 넣고 T9~T11을 앞당기되,
   **개선 전 수치를 vcsim 환경에서라도 먼저 남긴다.**

2. **T12(api.js 목 코드 분리)를 진행할 것인가?**
   CLAUDE.md의 "죽은 코드는 언급만" 원칙과 430줄이라는 규모가 충돌한다. 사용자 판단 필요.

3. **`resource_identities`에 `(rule, key_value)` UNIQUE를 지금 넣을 것인가?**
   현재 PK는 `(resource_id, rule, key_value)`라 **서로 다른 자원이 같은 식별키를 가질 수 있다.**
   Step 1은 `uq_vm_native`가 물리적으로 막아 주지만, 2·3순위가 들어오는 Step 4·5에서는
   보장이 사라진다. 지금 넣으면 마이그레이션 1건, 나중에 넣으면 데이터 정리가 필요하다.
   → **이 계획에는 넣지 않았다.** Step 4 착수 시 결정할 항목으로 남긴다.

4. **T5의 중복 요청 응답 계약** — 202 유지(상태 필드로 구분) vs 409.
   UI 폴링 로직과 함께 정해야 한다. 구현 시 확정.

## 이 계획이 다루지 않는 것

Step 1 범위 밖이므로 **의도적으로 제외**했다 (AD-4).

| 항목 | 해당 Step |
|---|---|
| IP·디스크·NIC·스냅샷 수집 | Step 4 |
| NSX opaque 백킹 3분기 (계획 04 §6.3) | Step 4 — Step 1엔 NIC 매핑 자체가 없다 |
| Host/Cluster/Datastore/Network 수집 | Step 6 |
| Custom Attributes (FR-606) | Step 8 |
| 연결 수정(PATCH), FR-109 2단계 확인 | Step 3 |
| Redis 분산 락, 주기 수집 워커 | Step 3 |
| VCF 9 실측 7항목 (ROADMAP §15.5) | Step 2 |

---

## 실행 결과 (2026-08-19)

### 완료

| 태스크 | 결과 |
|---|---|
| T1 | `tests/conftest.py`의 autouse DB 픽스처를 `tests/integration/conftest.py`로 이동. **단위 테스트 110건이 DB 없이 1.4초에 통과** (이전: 105건 전부 `ConnectionRefusedError`, 22.9초) |
| T2 | `[tool.ruff.lint] select` 13개 룰군 + per-file-ignores + FastAPI `extend-immutable-calls`. mypy strict override(pyVmomi·jose). `.github/workflows/ci.yml` 신규 (Python **3.11·3.13** 매트릭스, PostgreSQL 16 서비스, D-005·D-010 grep 검사 포함) |
| T3 | `VmSummaryResponse.configured_os` 추가 + `osField` 3분기 확장 + 목 데이터 계약 일치. **vcsim 실서버 관통에서 `configured_os='otherGuest'` 확인** |
| T4 | `_mark_unexpected_failure` 신규 (새 세션으로 기록), `ConnectionStatus.COLLECTION_ERROR` 추가, `mark_attempt`가 이전 오류를 지움 |
| T5 | `_running_collections` 집합 + 202 `already_running`. **실서버에서 2회 연속 요청 → `accepted` / `already_running` 확인** |
| T6 | `pyvmomi>=8.0.3,<10` |
| T7 | `_build_ssl_context`에 `minimum_version = TLSv1_2` |
| T8 | 어댑터 조회 시간·DB 반영 시간 **분리** 계측 (`adapter_ms`/`db_ms`/`batch_count`/`vm_count`) |

### 계획에 없던 발견 — 린트·타입 게이트가 잡아낸 것

품질 게이트(T2)를 실제로 켜자 **계획 수립 시점에 몰랐던 결함 4건**이 드러났다.
전부 "테스트는 통과하는데 실제로는 잘못 동작하던" 종류다.

| # | 결함 | 영향 |
|---|---|---|
| 1 | `logger.info(..., extra={"created": ...})` — `created`는 **LogRecord 예약 필드**라 `KeyError` | **모든 정상 수집이 "수집 중 예외"로 로그에 기록되고 있었다.** 데이터는 커밋된 뒤라 화면에는 정상으로 보여 로그를 봐야만 드러났다 (ruff `G101`) |
| 2 | `migrations/env.py`의 `fileConfig`가 `disable_existing_loggers=True`(기본값) | 같은 프로세스에서 마이그레이션을 돌리면 **애플리케이션 로거가 전부 죽는다.** T8 계측 테스트가 "레코드 0건"으로 실패해 발견 |
| 3 | `scripts/arch_check.py`의 f-string 백슬래시가 **Python 3.11에서 문법 오류** | `requires-python = ">=3.11"`인데 3.11 환경에서 arch_check 실행 불가. CI 3.11 매트릭스가 잡는다 (ruff) |
| 4 | `collector._create_view_sync`가 `content.viewManager`의 None을 확인하지 않음 | 권한 부족 시 `AttributeError`가 나서 **원인이 "권한 부족"이라는 것이 메시지에서 사라진다** (mypy `union-attr`) |

추가로 `require()`의 반환 타입 선언 오류(`Callable[..., AuthenticatedUser]` → `Awaitable`),
`async_sessionmaker` 제네릭 인자 누락, `list[dict]` 요소 타입 누락,
`retry.py`의 프로덕션 `assert`(`python -O`에서 제거됨)를 함께 고쳤다.

### 검증 환경 주의

**`pytest`와 개발 서버(uvicorn)를 동시에 돌리면** `test_scvmm_mock_fabric.py`의
teardown `TRUNCATE`에서 `DeadlockDetectedError`가 난다 (7건 error).
서버를 끄면 전부 통과한다. 테스트 전에 개발 서버를 내린다.

### 남은 것

- **Phase 4 (T9~T11)** — C3(Step 2 실환경 실측) 통과 후. T8 계측이 병목 판정 근거를 만든다
- **Phase 5 (T12·T13)** — 사용자 승인 대기
- **CI 실제 동작 확인** — 워크플로는 작성했으나 push 전이라 실행된 적이 없다
- Open Questions 1~3 — 아직 결정되지 않았다 (4번은 T5 구현 시 확정)
