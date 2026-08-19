# 구현 계획서 목록

> 작성일: 2026-08-06
> 근거: `spec.md` (요건), `docs/00_research_notes.md` (조사), `docs/02_decision.md` (결정)

## ⚑ 먼저 읽을 것 — [ROADMAP.md](ROADMAP.md)

아래 계획서 01~14는 **영역별(수직) 설계서**다. 그대로 따라가면 모든 영역을 완성한 뒤에야 첫 화면이 뜬다.
(15부터는 특정 Step의 실행 계획이며 여러 영역을 가로지른다.)

**[ROADMAP.md](ROADMAP.md)** 는 이 계획서들을 동작하는 얇은 조각으로 다시 자른 **실행 순서**다.
Step 1(**화면 디자인 확정** + vCenter 1개 등록 → VM 목록)을 먼저 만들어 실제 환경에 적용하고,
거기서 얻은 사실로 다음 조각을 진행한다. **Wave 0의 디자인(11 Part A)은 Step 1에 포함**되며 백엔드와 병렬로 진행한다.

**구현에 착수할 때는 ROADMAP의 Step 정의를 기준으로 이 계획서들의 필요한 절만 골라 구현한다**
(단계 ↔ 계획서 절 매핑은 ROADMAP §24).
아래 Wave 구성은 영역 간 의존 관계를 나타내며, ROADMAP의 Step은 그 위에서 실행 순서를 정한다.

## 계획서 읽는 법

각 계획서는 **구현 가능한 상세도**로 작성되어 있다. 코드·SQL·스크립트는 구현의 출발점이며,
그대로 붙여넣는 완성 코드가 아니라 **구조와 주의점을 고정하기 위한 골격**이다.

| 표기 | 의미 |
|---|---|
| `[검증 필요]` | 조사 시점 2차 자료 기반. 구현 전 실제 환경·라이브러리로 확인 (`docs/00_research_notes.md` §11) |
| `[TODO]` | 사용자 확정이 필요한 요건. 임시 기본값으로 구현하되 설정으로 변경 가능하게 (§5) |
| **굵은 경고** | 빠뜨리면 데이터 손실·보안 사고로 이어지는 지점 |

각 계획서 말미의 **완료 기준**은 verifier의 검증 항목이자 implementer의 자체 점검 목록이다.

## 0. 계획서 목록과 Wave 배치

| 파일 | 영역 | Wave | 계층 | 주요 요건 |
|---|---|---|---|---|
| [**ROADMAP.md**](ROADMAP.md) | **단계적 실행 순서 (최소 기능 우선)** | — | — | — |
| [01-project-structure.md](01-project-structure.md) | 디렉토리·설정·환경변수 | 0 | 전체 | — |
| [11-web-ui.md](11-web-ui.md) **Part A** | **Claude Design으로 화면 디자인 → Claude Code 핸드오프** | **0** | — | FR-1207~1212 |
| [02-domain-model.md](02-domain-model.md) | 자원 엔티티, 정규화 매핑, CI 식별 | 1 | domain | FR-301·302·304, §2 |
| [03-inventory-reader-port.md](03-inventory-reader-port.md) | 커넥터 Protocol, 수집 결과 계약 | 1 | domain | FR-301, FR-501, NFR-202 |
| [10-security-audit.md](10-security-audit.md) | 자격증명 암호화, 감사 로그 | 1 | infrastructure | NFR-203·208·209, FR-1004 |
| [06-collection-scheduler.md](06-collection-scheduler.md) | DB 스키마·저장소, 수집 스케줄러 | 1·3 | infra·orchestration | FR-2xx, FR-303·305·307 |
| [04-vcenter-adapter.md](04-vcenter-adapter.md) | pyVmomi 수집 어댑터 | 2 | infrastructure | FR-203, §2.2 |
| [05-hyperv-adapter.md](05-hyperv-adapter.md) | WinRM/WMI/KVP 수집 어댑터 | 2 | infrastructure | FR-103, §2.2, CST-06 |
| [09-auth-rbac.md](09-auth-rbac.md) | 인증, 역할, 조회 범위 제한 | 2 | domain·infra | FR-1001~1003, NFR-204·210 |
| [07-inventory-query.md](07-inventory-query.md) | 조회·검색·IP역조회, 메타데이터 | 3 | application | FR-4xx, FR-5xx, FR-6xx |
| [12-change-history.md](12-change-history.md) | 변경 이력, 수명주기, 데이터 품질 | 3 | application | FR-7xx, FR-501~505 |
| [08-api-server.md](08-api-server.md) | FastAPI 엔드포인트, 연결 관리 API | 4 | interface | FR-1xx, FR-11xx |
| [11-web-ui.md](11-web-ui.md) **Part B** | 포탈 화면 구현, 대시보드 | 4 | interface | FR-12xx, FR-9xx |
| [13-report-export.md](13-report-export.md) | 리포트, Excel/CSV 내보내기 | 4 | application·interface | FR-8xx |
| [14-polestar-enrichment.md](14-polestar-enrichment.md) | **폴스타 연동 — 게스트 관점 자원 정보 보강** | — (Step 9 전용, Wave 4 이후) | domain·infra·app·interface | 신규 요건 (D-019), FR-304·501·601 |
| [15-resource-query-revision.md](15-resource-query-revision.md) | **자원(VM) 조회 기능 수정 — 표시 정보 카탈로그 + IP·검색·상세** | — (Step 4 전용, 07·04·05·06·11을 가로지름) | 전체 | FR-4xx, FR-501·502, 보완 FR-410~414·506~508 |
| [00-claude-skills-plugins.md](00-claude-skills-plugins.md) | Claude Code 스킬 활용 | — | — | — |

## 1. 의존 관계 그래프

```
Wave 0  ┌─ 01-project-structure (디렉토리·설정 스켈레톤)
        │
        └─ 11-web-ui Part A  Claude Design 캔버스에서 화면 디자인
             │               → 내부 URL 검토 → Handoff to Claude Code
             │               → 번들 토큰을 docs/03_design_system.md에 고정
             │        └─ 백엔드와 병렬 진행. Wave 4 이전에만 끝나면 된다
             ▼
Wave 1  ┌─ 02-domain-model ──┬─ 03-inventory-reader-port
        ├─ 10-security-audit │
        └─ 06(저장소 부분) ◄─┘
             │
             ▼
Wave 2  ┌─ 04-vcenter-adapter ─┐  (서로 참조 금지 — worktree 병렬 최적)
        ├─ 05-hyperv-adapter ──┘
        └─ 09-auth-rbac
             │
             ▼
Wave 3  ┌─ 06(스케줄러 부분)
        ├─ 07-inventory-query
        └─ 12-change-history
             │
             ▼
Wave 4  ┌─ 08-api-server
        ├─ 11-web-ui Part B ◄── docs/03_design_system.md (Wave 0 산출물)
        └─ 13-report-export
```

**Wave 0의 디자인은 백엔드 구현과 무관하므로 전 과정과 병렬로 진행한다.**
다만 **Wave 4 착수 전에는 반드시 확정**되어야 한다. 디자인 없이 화면을 만들면 재작업이 발생하고,
목록에 노출할 필드가 정해지지 않아 API 응답 형태도 확정할 수 없다.

**Wave 0 착수 전 확인**: Claude Design은 Pro/Max/Team/Enterprise 플랜이 필요하며
Enterprise는 관리자 활성화가 선행되어야 한다. 사용할 수 없으면 계획 11 §10의 대체 경로로 전환한다.

**Wave 병렬 실행**: 같은 Wave 내 계획서는 담당 디렉토리가 겹치지 않으므로 worktree 격리로 동시 구현한다.
공유 파일(`src/config.py`, `src/domain/ports.py`, `pyproject.toml`)은 Wave 종료 후 팀 리드가 통합한다.

## 2. Phase ↔ Wave 대응

`spec.md` §6의 Phase는 사용자에게 보이는 기능 단위, Wave는 **영역 간 의존 순서** 단위다.
실제 구현 착수 순서는 [ROADMAP.md](ROADMAP.md)의 **Step**을 따른다 (Step ↔ Phase 대응은 ROADMAP §3).

| spec Phase | 완료 기준 | 해당 Wave |
|---|---|---|
| Phase 0 | 주요 화면 시안 승인, 디자인 토큰 확정 | Wave 0 (11 Part A) |
| Phase 1 | 다수 vCenter·Hyper-V에서 VM 목록과 필수 속성이 통합 조회됨 | Wave 0~3 (07의 기본 조회까지) |
| Phase 2 | 브라우저에서 IP로 VM을 1초 내 찾을 수 있음 | Wave 3~4 (07 검색 + 08 API + 11 Part B) |
| Phase 3 | 소유자 정보가 재수집에도 보존되고 변경 이력이 남음 | Wave 3~4 (07 메타데이터 + 12) |
| Phase 4 | 스냅샷·유휴 자원 리포트와 Excel 내보내기 동작 | Wave 4 (13) |

## 3. 모든 계획서에 공통 적용되는 규칙

### 3.1 아키텍처 계층

```
domain → config/utils → infrastructure → application → orchestration → interface → entry
```

새 모듈을 만들면 `scripts/arch_check.py`의 `MODULE_LAYER_MAP`에 등록한다. 등록하지 않으면 검사에서 조용히 제외된다.

**특화 규칙 3개** (`.claude/skills/arch-check.md`):
1. `application`/`orchestration` → `infrastructure.vcenter|hyperv` 직접 import 금지
2. 어댑터 간 교차 참조 금지 (`vcenter` ↔ `hyperv`)
3. 커넥터 Protocol·어댑터의 public 메서드에 자원 변경 접두사 금지

### 3.2 읽기 전용 (D-005)

하이퍼바이저에 쓰기·제어 API를 호출하지 않는다. 조회 메서드는 `get_`/`list_`/`fetch_`/`collect_`/`read_`/`search_`/`count_` 로 명명한다.

### 3.3 명명 규칙

| 대상 | 규칙 | 예 |
|---|---|---|
| 모듈·함수·변수 | snake_case | `collect_virtual_machines` |
| 클래스·타입 | PascalCase | `VirtualMachine`, `CollectionResult` |
| 상수 | UPPER_SNAKE | `DEFAULT_TIMEOUT_SECONDS` |
| DB 테이블 | 복수형 snake_case | `virtual_machines`, `collection_runs` |
| API 경로 | kebab-case 복수형 | `/api/v1/virtual-machines` |
| Protocol | `~Reader` / `~Repository` | `HypervisorInventoryReader` |

### 3.4 공통 기술 규칙

- 모든 함수에 타입 힌트, 독스트링은 한국어
- I/O 바운드는 `async`. pyVmomi 등 동기 라이브러리는 `asyncio.to_thread`로 오프로드
- 원격 호출에 타임아웃 필수(기본 60초), 재시도 최대 3회
- **인증 실패는 재시도하지 않는다** (FR-114, 계정 잠금 방지)
- 대량 조회는 페이징·인덱스 전제. 전체 로드 후 메모리 필터링 금지
- 시각은 UTC `datetime`으로 저장하고 표시 시점에 변환

### 3.5 에러 처리 계층 규약

```
어댑터        하이퍼바이저 예외 → 도메인 예외로 변환 (pyVmomi 예외를 밖으로 내보내지 않음)
저장소        DB 예외 → 도메인 예외
유스케이스    도메인 예외를 처리하거나 그대로 전파
API           도메인 예외 → HTTP 상태 코드 매핑
```

도메인 예외 계층 (`src/domain/exceptions.py`, 계획서 02에서 정의):

```
PortalError
├─ ConnectionError          연결 관련
│  ├─ AuthenticationError   인증 실패 → 재시도 금지 (FR-114)
│  ├─ UnreachableError      네트워크·서버 오류 → 재시도 대상
│  └─ PermissionError       권한 부족
├─ CollectionError          수집 중 오류
├─ NotFoundError            자원 없음
└─ ValidationError          입력 검증 실패
```

### 3.6 테스트 규칙

- pytest + pytest-asyncio
- **계약 테스트**: 동일 스위트를 vCenter/Hyper-V 어댑터 양쪽에 파라미터화 적용
- **정합성 테스트**: 2회 수집 → 레코드 1건 / 메타데이터 보존 / 자원 소실 → 유예 / 호스트 변경 → 이력
- 실제 하이퍼바이저 연결 테스트는 작성하지 않는다 (목 커넥터 사용, CST-04)

## 4. 요건 커버리지 매트릭스

| 요건 그룹 | 담당 계획서 |
|---|---|
| FR-1xx 연결 관리 | 08(API), 11(UI), 10(자격증명), 06(연결 상태) |
| FR-2xx 수집·동기화 | 06(스케줄러), 04·05(어댑터) |
| FR-3xx 정규화·정합성 | 02(모델·식별 규칙), 06(저장소 반영) |
| FR-4xx 조회·검색 | 07(유스케이스), 08(API), 11(UI) |
| FR-5xx 데이터 품질 | 12(판정), 07(조회), 11(표시) |
| FR-6xx 메타데이터 | 07(유스케이스), 08(API), 11(UI) |
| FR-7xx 변경 이력 | 12 |
| FR-8xx 리포트 | 13 |
| FR-9xx 대시보드 | 11, 13(집계 쿼리) |
| FR-10xx 인증·권한·감사 | 09, 10(감사 로그) |
| FR-11xx 외부 API | 08 |
| FR-12xx 웹 UI | 11 (Part A 디자인 + Part B 구현) |
| NFR-1xx 성능 | 06(인덱스), 07(쿼리) |
| NFR-2xx 보안 | 09, 10 |
| NFR-3xx 안정성 | 06 |
| NFR-4xx 사용성 | 11 |

## 5. 미확정 항목이 계획에 미치는 영향

`spec.md`의 `[TODO]`가 확정되어야 완결되는 계획 항목이다. 확정 전에는 **기본값으로 구현하되 설정으로 변경 가능하게** 만든다.

| 미확정 항목 | 영향받는 계획서 | 임시 기본값 |
|---|---|---|
| 관리 규모 (NFR-104) | 06(인덱스·배치 크기) | VM 5,000건 가정 |
| 수집 주기 (NFR-105) | 06 | 6시간 |
| 삭제 자원 유예 기간 (FR-307) | 06, 12 | 7일 |
| 이력 보존 기간 (FR-706) | 12 | 1년 |
| 연결 삭제 시 자원 처리 (FR-109) | 06, 08 | 보존(권장안) |
| 암호화 키 관리 (NFR-208) | 10 | 환경변수 |
| ~~SCVMM 도입 여부 (CST-09)~~ | 05 | **확정됨 (2026-08-07)** — 도입. SCVMM이 주 수집 경로이고 호스트 직접 연결은 미관리 호스트에만 사용 (D-012) |
| 외부 인증 연동 (FR-1005) | 09 | 로컬 계정만 |
