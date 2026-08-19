# Step 1 구현 개선 — 태스크 목록

> 상세는 `tasks/plan.md`. 각 태스크의 수락 기준·검증 절차는 거기에 있다.
> 작성일 2026-08-19 · **Phase 0~2 + T8 완료 (2026-08-19)**. Phase 4는 C3 대기, Phase 5는 승인 대기.

## Phase 0: 개발 루프 복구

- [x] **T1** 단위 테스트를 DB 없이 실행 가능하게 분리 — `tests/conftest.py`의
      autouse DB 픽스처를 `tests/integration/`으로 이동
      · 의존: 없음 · 규모: S
      · 검증: DB 정지 상태에서 `pytest tests/unit` 통과 / DB 기동 후 전체 통과
- [x] **T2** 린트·타입 검사 정비와 CI 게이트 — `[tool.ruff.lint] select` 추가
      (기존 `noqa: BLE001/SLF001/ARG001`이 가리키는 룰이 꺼져 있다), `ruff`·`mypy` 통과,
      CI 워크플로 신규
      · 의존: T1 · 규모: M
      · 검증: `ruff check` · `mypy src` · `arch_check --ci` · `pytest` 4종 exit 0

### ✅ Checkpoint C1: 개발 루프

- [x] `pytest tests/unit`이 PostgreSQL 없이 전부 통과 — 110건 / 1.4초 (이전: 105건 전부 에러 / 22.9초)
- [x] `ruff check` · `mypy src` · `arch_check --ci` 로컬 통과 — 위반 0
- [x] CI 워크플로 작성 (`.github/workflows/ci.yml`, Python 3.11·3.13 매트릭스) — **실제 CI 실행 확인은 push 후**
- [ ] **사람 검토 후 진행** ← 대기 중

---

## Phase 1: 요건 대비 기능 갭

- [x] **T3** 구성값 OS(`configured_os`)를 응답·화면까지 관통 — **최우선**
      `VmSummaryResponse`에 필드가 없어 Tools 미설치 VM의 OS가 화면에서 사라진다
      (ROADMAP §5.3 "게스트 OS 컬럼이 이 MVP의 시금석")
      · 의존: T1, T2 · 규모: M
      · 검증: 단위 + `test_api.py` 응답 계약 + vcsim 브라우저 확인 (vcsim은 게스트
      도구가 없어 **모든 VM이 이 경로를 탄다**)
- [x] **T4** 백그라운드 수집 실패를 연결 상태에 기록 — `_run_collection`이
      `mark_failure`를 호출하지 않아 UI가 영구 "수집 중…"에 머무른다
      · 의존: T1, T2 · 규모: S
      · 검증: 예외 주입 시 `last_error` 채워짐 / 오류 메시지에 자격증명 없음 /
      기존 수집 데이터 보존(NFR-302)
- [x] **T5** 동일 연결의 수집 중복 실행을 서버에서 차단 — UI 버튼 비활성만으로는
      다른 탭·API 직접 호출을 막지 못한다
      · 의존: T4 · 규모: S
      · 검증: 연속 2회 요청 → 작업 1회 / 완료 후 재요청 정상 / **단일 프로세스
      전제임을 주석에 명시** (Step 3 분산 락으로 교체)

### ✅ Checkpoint C2: 기능 갭

- [x] Tools 미설치 VM 화면에 구성값 OS가 보인다 — vcsim 실서버 관통 + `osField` 5경우 검증
- [x] 수집 실패 시 UI가 "수집 중…"에서 벗어나 사유를 보여준다
- [x] 수집 버튼 연타에도 백그라운드 작업이 하나만 뜬다 — 실서버에서 2회 연속 요청 → `already_running`
- [x] 회귀 없음 — 전체 테스트 통과, vcsim 관통 재확인
- [ ] **사람 검토 후 진행** ← 대기 중

---

## Phase 2: 최신 VCF 대응 (D-020 중 코드로 닫히는 것)

- [x] **T6** `pyvmomi` 버전 핀 (`>=8.0.3,<10`) — 현재 무핀이고 9.1.0.0이 설치되어 있다.
      Broadcom 호환 정책은 직전 4개 릴리스 (계획 04 §14.2)
      · 의존: 없음 · 규모: XS
      · 검증: 재설치 후 `pytest` 통과
- [x] **T7** TLS 하한 명시 — `_build_ssl_context`에 `minimum_version` (계획 04 §3.1)
      · 의존: 없음 · 규모: XS
      · 검증: 단위 2경로 + vcsim 관통

---

## ⏸ Checkpoint C3: Step 2 실환경 실측 — **이 계획의 태스크가 아님**

> Phase 4는 이 체크포인트 없이 시작하지 않는다 (`tasks/plan.md` AD-1).

- [x] **T8** 수집 구간별 소요 시간 계측 — **어댑터 조회 시간과 DB 반영 시간을 분리**
      기록. 총 시간만 재면 Phase 4에서 무엇을 고칠지 정할 수 없다
      · 의존: T1, T2 · 규모: S
      · 검증: fake 리더 수집 후 두 구간 로그 확인 / 로그에 자격증명·개별 VM 이름 없음
- [ ] ROADMAP §15의 25개 항목 실측 → `docs/04_field_validation.md`
- [ ] 병목이 어댑터 조회인지 DB 반영인지 판정됨

---

## Phase 4: 성능 최적화 — **C3 통과 후에만 착수**

- [ ] **T9** upsert를 배치 조회 기반으로 전환 — 현재 VM 1건당 4~5 쿼리.
      5,000 VM이면 2만 회 이상 왕복
      · 의존: C3, T8 · 규모: M (로직 난이도 높음)
      · 검증: **모호성 감지(`limit(2)`) 보존** / 기존 식별·폴백 테스트 3종 통과 /
      쿼리 수가 배치 크기에 비례하지 않음
      · ⚠ 리스크 높음 — CI 식별 규칙이 미묘하게 바뀌면 중복 레코드 발생
- [ ] **T10** 식별 행 동기화를 변경분에만 적용 — 현재 매 수집마다 무조건 DELETE+INSERT
      · 의존: T9 · 규모: S
      · 검증: 재수집 후 행 수 불변 / 키 변경 시 갱신됨
- [ ] **T11** `ServiceInstanceContent` 캐시 — `content` 접근마다 `RetrieveContent()`
      SOAP 왕복. 페이지마다 발생한다
      · 의존: C3 · 규모: XS
      · 검증: 세션당 호출 1회 / 닫힌 세션 접근 시 기존 동작 유지 / 페이징 테스트 4종

### ✅ Checkpoint C4: 성능

- [ ] 동일 환경·동일 VM 수에서 수집 시간이 **측정값으로** 개선됨
- [ ] 2회 수집 후 VM 1건(FR-303) · 식별 행 수 불변 — 회귀 없음
- [ ] 개선 전후 수치를 `docs/04_field_validation.md`에 병기

---

## Phase 5: 정리 — **선택 · 사용자 승인 후**

- [ ] **T12** `static/js/api.js`의 목 구현 분리 — 632줄 중 약 430줄이 `USE_MOCK=false`
      경로의 죽은 코드. 상수 한 글자로 인증이 클라이언트 목으로 대체된다
      · 의존: C2 · 규모: M
      · ⚠ CLAUDE.md "죽은 코드는 언급만" 원칙과 충돌 — 승인 필요
- [ ] **T13** `hypervisor` 판정의 문자열 하드코딩 제거 — `kind == "vcenter"` 리터럴 비교
      · 의존: C2 · 규모: S
      · 검증: 4개 `ConnectionKind` 전부 매핑 테스트

---

## 결정 대기 (`tasks/plan.md` Open Questions)

- [ ] 성능 최적화를 Step 2 실측 뒤로 미룰 것인가 (권고: 미룬다 — ROADMAP §14)
- [ ] T12를 진행할 것인가
- [ ] `resource_identities`에 `(rule, key_value)` UNIQUE를 지금 넣을 것인가
      (이 계획에는 미포함 — Step 4 착수 시 결정)
- [x] T5의 중복 요청 응답 계약 → **202 유지 + `status: already_running`**으로 결정.
      409로 만들면 UI가 `catch`로 떨어져 **오류 토스트**를 띄운다. "이미 진행 중"은
      오류가 아니며, 기존 `{"status": "accepted"}` 계약을 확장만 하므로 UI 회귀도 없다.
