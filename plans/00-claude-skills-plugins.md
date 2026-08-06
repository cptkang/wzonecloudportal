# 00. Claude Code 스킬 및 플러그인 활용 계획

> 작성일: 2026-08-06 (v0.2 — 읽기 전용 인벤토리 포탈 요건 확정 반영)
> 목적: 통합 자원 인벤토리 포탈 개발/테스트에 활용 가능한 Claude Code 스킬·도구를 정리하고 Wave별 활용 방안 확정
> 출처: collectorinfra 프로젝트의 `plans/18-claude-skills-plugins.md`를 이 프로젝트에 맞게 재작성

---

## 1. 프로젝트 기술 요소와 스킬 매핑

| 프로젝트 기술 요소 | 관련 스킬/도구 | 우선순위 |
|---|---|---|
| 계층 격리 + 어댑터 결합 + **읽기 전용 범위** 강제 | **arch-check** (커스텀) | 최상 |
| 자격증명 보호·조회 범위 격리 | **security-review** | 최상 |
| FastAPI 조회 API + 포탈 UI | **Playwright MCP** | 높음 |
| 자원 현황 대시보드 | **dataviz** | 중간 |
| 인벤토리 Excel 내보내기 | **xlsx** | 중간 |
| 아키텍처·수집 흐름 문서화 | **mermaid-tools** | 중간 |
| 코드 품질·리뷰 | **code-review**, **simplify** | 중간 |
| 프로젝트 문서 관리 | **claude-md-management** | 중간 |

---

## 2. Tier 1: 품질 게이트 (필수)

### 2.1 arch-check (커스텀 스킬)

**용도**: 계층 위반 + 하이퍼바이저 어댑터 결합 위반 + **읽기 전용 범위 위반** 탐지

정의: `.claude/skills/arch-check.md`, 구현: `scripts/arch_check.py`

**검사 규칙**:
- 일반 계층 규칙 5개 (domain 무의존, 역방향 import 금지 등)
- 특화 규칙 A-1: `application`/`orchestration` → `infrastructure.vcenter|hyperv` 직접 import 금지
- 특화 규칙 A-2: 어댑터 간 교차 참조 금지 (`vcenter` ↔ `hyperv`)
- **특화 규칙 B: 커넥터 Protocol·어댑터의 public 메서드에 자원 변경 접두사 금지** (`create_`, `delete_`, `power_`, `migrate_` 등)

**호출**:
```bash
python scripts/arch_check.py --ci        # 승인 전 필수, 위반 시 exit 1
python scripts/arch_check.py --verbose   # 의존성 매트릭스 확인
python scripts/arch_check.py --json      # kind 필드로 dependency / readonly 구분
```

**적용 시점**: 코드 변경이 포함된 모든 Wave의 승인 전.
특히 `src/domain/ports.py`와 어댑터 변경 시 필수.

**주의**: 이 검사는 메서드명 기반이므로 **어댑터 내부의 실제 쓰기 API 호출은 잡지 못한다.**
`get_vm_status()` 안에서 `PowerOffVM_Task()`를 호출하면 통과한다. 실제 호출 대상 확인은 verifier의 코드 리뷰 항목이다 (D-005 한계 참조).

### 2.2 security-review

**용도**: 이 프로젝트의 최대 위험 영역 점검

**중점 확인 항목**:
- 하이퍼바이저 자격증명이 로그·예외·API 응답·`__repr__`에 노출되는지
- 조회 범위(연결·자원 그룹) 필터링을 우회하는 경로가 있는지
- 권한 검사가 API 계층에만 있고 유스케이스 계층에 누락되지 않았는지
- 인증서 검증 비활성화(`verify=False` 등)가 코드에 남아 있는지
- **인벤토리 정보 자체가 민감 정보**이므로 내보내기·API 응답에 과도한 정보가 노출되지 않는지

**호출**: `/security-review`

**적용 시점**: 인증·권한·자격증명 관련 코드 변경 시 (사실상 매 Wave)

### 2.3 code-review

**용도**: 상태 계약 위반, 리소스 해제 누락, 에러 처리 결함 감지

**중점 확인 항목**:
- 하이퍼바이저 세션/커넥션 미해제
- 타임아웃 미설정 원격 호출, 재시도 상한 없는 루프
- 비동기 코드에서의 블로킹 호출 (pyVmomi 동기 호출을 스레드 오프로드 없이 사용)
- 대량 자원 조회 시 전체 로드 후 메모리 필터링 (페이징·인덱스 미사용)

**호출**: `/code-review`

### 2.4 simplify

**용도**: 코드 중복·복잡도 개선

**활용 시나리오**:
- vCenter/Hyper-V 어댑터의 중복 정규화 로직 → 공통 유틸 추출
  (단, **어댑터 간 직접 참조가 아닌 `utils`/`domain` 경유**여야 함 — 특화 규칙 A-2)
- 유스케이스 간 공통 권한 필터·감사 로그 패턴 정리

**호출**: `/simplify`

---

## 3. Tier 2: 개발·검증 지원

### 3.1 Playwright MCP 도구

**용도**: FastAPI 조회 엔드포인트 및 포탈 UI E2E 테스트

**전제**: 서버 기동 (`uvicorn src.main:app --port 8080`) + **목(mock) 커넥터** 사용

**사용 가능한 도구**:
```
browser_navigate         → API 엔드포인트/화면 접근
browser_fill_form        → 로그인·검색 폼 입력
browser_click            → 버튼 클릭, 필터 적용
browser_snapshot         → 페이지 상태 캡처
browser_network_requests → API 호출 모니터링
browser_console_messages → JS 에러 감지
browser_evaluate         → API 응답 JSON 검증
```

**핵심 시나리오**:

| # | 시나리오 | 검증 내용 | 관련 요건 |
|---|---|---|---|
| 1 | 헬스체크 | `/api/v1/health` → `{"status": "healthy"}` | — |
| 2 | 통합 인벤토리 | 여러 vCenter와 Hyper-V 자원이 동일 포맷으로 함께 표시 | FR-401 |
| 3 | **IP 역조회** | IP 입력 → 해당 VM 즉시 검색 (최다 사용 시나리오) | FR-404 |
| 4 | 자원 상세·관계 탐색 | 소속 호스트·클러스터·데이터스토어로 이동 | FR-402, FR-409 |
| 5 | **데이터 품질 표시** | 도구 미설치 VM의 IP·OS가 빈칸이 아닌 "수집 불가(사유)" | FR-501 |
| 6 | 조회 범위 제한 | 권한 없는 연결의 자원이 목록·상세·API 어디에도 없음 | FR-1003 |
| 7 | 수집 상태 위젯 | 연결별 마지막 수집 시각, 실패 연결 강조 | FR-904 |
| 8 | Excel 내보내기 | 다운로드 파일 생성 및 내용 확인 | FR-801 |

**금지**: 운영 하이퍼바이저 연결 테스트

### 3.2 dataviz

**용도**: 자원 현황 대시보드 차트 설계

**활용 시나리오**:
- 하이퍼바이저별·연결별 VM 수 분포 (FR-903)
- 전원 상태 분포, 게스트 OS 분포 (FR-901, FR-806)
- vCPU·메모리·스토리지 할당량 대비 물리 용량 비율 (FR-902)
- 데이터스토어 사용률·오버커밋 비율 (FR-805)
- 라이트/다크 테마 모두에서 읽히는 색상 체계 확보

**적용 Wave**: Wave 4 (UI)

### 3.3 xlsx

**용도**: 인벤토리 Excel 내보내기 기능 검증

**활용 시나리오**:
- 자원 목록 내보내기 결과의 헤더·데이터 정합성 검증 (FR-801)
- 대량 행(수천~수만 VM) 출력 시 파일 구조·성능 확인
- 메타데이터 일괄 가져오기(FR-605)용 템플릿 양식 생성 및 파싱 검증

**적용 Wave**: Wave 4

### 3.4 mermaid-tools

**용도**: 아키텍처·수집 흐름 다이어그램 생성

**활용 시나리오**:
- `docs/` 아키텍처 문서의 계층 구조도
- 수집 경로 / 조회 경로 분리 흐름도 (D-007)
- 하이퍼바이저 고유 모델 → 공통 모델 정규화 매핑도
- 자원 관계(VM ↔ Host ↔ Cluster ↔ Datastore ↔ Network) 다이어그램

---

## 4. Tier 3: 프로젝트 관리

| 스킬 | 호출 | 용도 |
|---|---|---|
| **claude-md-management** | `/revise-claude-md`, `/claude-md-improver` | Phase 완료 시 CLAUDE.md 아키텍처·명령어 갱신 |
| **loop** | `/loop` | 수집 워커 등 장기 실행 태스크 모니터링 |
| **run** | `/run` | 포탈 앱 기동 및 변경 사항 실동작 확인 |

---

## 5. Wave별 스킬 매핑

| Wave | 주요 작업 | 권장 스킬 |
|---|---|---|
| Wave 1 (도메인·보안·저장소) | 자원 모델, Protocol, 자격증명 암호화, 저장소 | **arch-check**(읽기 전용 검사), **security-review**, code-review |
| Wave 2 (수집 어댑터) | vCenter/Hyper-V 어댑터 | **arch-check**(교차 참조 + 읽기 전용), code-review |
| Wave 3 (유스케이스·워커) | 조회·검색, 수집 스케줄러, 변경 이력 | arch-check, security-review, simplify |
| Wave 4 (API·UI·리포트) | 엔드포인트, 포탈 화면, 대시보드, 내보내기 | **Playwright MCP**, **dataviz**, **xlsx**, security-review |
| 전 Wave | 문서·품질 관리 | mermaid-tools, claude-md-management |

---

## 6. 현재 환경에 미설치된 스킬

collectorinfra의 계획서에는 아래 스킬이 포함되어 있었으나, 현재 이 환경의 스킬 목록에는 없습니다.
필요해지면 플러그인 설치 후 이 문서에 추가합니다.

| 스킬 | collectorinfra에서의 용도 | 이 프로젝트에서의 대체 |
|---|---|---|
| `webapp-testing` | Playwright 기반 E2E | **Playwright MCP 도구 직접 사용** (기능 동일) |
| `frontend-design` | UI 디자인 시스템 | Wave 4에서 필요 시 설치 검토. 차트는 `dataviz`로 대체 |
| `mcp-builder` | MCP 서버 도구 정의 | 이 프로젝트에는 MCP 서버 구성요소가 없어 제외 |
| `skill-creator` | 커스텀 스킬 생성 | 반복 작업 확인 시 설치 검토 |

---

## 7. 스킬 호출 방법 요약

| 스킬 | 호출 | 설명 |
|---|---|---|
| **arch-check** | `/arch-check` 또는 `python scripts/arch_check.py` | 계층 + 어댑터 결합 + 읽기 전용 범위 (커스텀) |
| **security-review** | `/security-review` | 보안 점검 |
| **code-review** | `/code-review` | 코드 리뷰 |
| **simplify** | `/simplify` | 복잡도·중복 개선 |
| **Playwright MCP** | `browser_*` 도구 직접 호출 | E2E 테스트 |
| **dataviz** | 차트 작성 전 로드 | 차트 설계 |
| **xlsx** | Excel 작업 시 자동 트리거 | 스프레드시트 검증 |
| **mermaid-tools** | 다이어그램 작업 시 | 다이어그램 생성 |
| **claude-md-management** | `/revise-claude-md` | CLAUDE.md 갱신 |
| **loop** | `/loop` | 주기적 모니터링 |
| **run** | `/run` | 앱 기동 확인 |
