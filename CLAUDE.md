# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

다수의 VMware vCenter 인스턴스와 다수의 Hyper-V 호스트/클러스터에 존재하는 가상자원 정보를 자동 수집·정규화하여,
단일 포탈에서 통합 조회·검색·리포팅하는 **읽기 전용 자원 인벤토리 포탈**(CMDB 성격).

전체 요건은 `spec.md`에 있습니다.
아키텍처 의사결정과 근거는 `docs/02_decision.md`에 기록합니다 — **team-lead 에이전트는 변경 전 이 파일을 반드시 확인하고, 새 결정이 생기면 갱신해야 합니다.**

**요건의 근거와 기술 참고 자료는 `docs/00_research_notes.md`에 있습니다.**
- 요건이 왜 그렇게 정해졌는지 모를 때 → §10 조사→요건 추적표를 역참조
- vCenter/Hyper-V 수집 API·속성명·클래스명이 필요할 때 → §4·§5
- 수집이 실패하거나 값이 비는 원인을 찾을 때 → §6 도구 의존성
- **구현 전 재검증이 필요한 항목** → §11 (조사 시점 2차 자료라 실제 환경 확인 필요)

> **이 프로젝트는 자원을 생성·변경·삭제하지 않습니다.** VM 프로비저닝, 전원 제어, 스냅샷 조작, 리소스 변경, 마이그레이션은
> 명시적 비목표입니다 (`spec.md` §1.2, CST-01). 관련 기능 요청을 받으면 구현하지 말고 사용자에게 범위 확인을 요청하세요.

## Architecture

**수집 경로와 조회 경로가 분리**되어 있습니다. 사용자 조회 요청은 하이퍼바이저를 직접 호출하지 않습니다.

```
[수집 경로 — 주기적, 읽기 전용]
  src/orchestration (수집 스케줄러)
        │  Protocol 주입
        ▼
  src/domain/ports.HypervisorInventoryReader
        ▲                          ▲
  infrastructure/vcenter    infrastructure/hyperv
   (pyVmomi PropertyCollector)  (WinRM / WMI / KVP)
        │  하이퍼바이저 고유 모델 → 공통 자원 모델 정규화
        ▼
  infrastructure/repository ──► PostgreSQL (인벤토리·이력)

[조회 경로 — 저장소 기반]
  브라우저 ──► static/ (포탈 UI) ──► src/api ──► src/application ──► repository ──► PostgreSQL / Redis
```

**서비스는 웹 브라우저로 제공된다** (`spec.md` FR-1201). 운영자가 별도 클라이언트 없이 브라우저에서
모든 조회·관리를 수행한다. 단, **자원을 변경하는 UI 요소는 제공하지 않는다** (FR-1206).

화면 디자인은 **구현 전에 Claude Artifacts로 시안을 만들어 확정**한다 (FR-1212, D-009).
확정된 토큰·컴포넌트 규격은 `docs/03_design_system.md`에 있으며, `static/` 구현은 이를 따른다.

**핵심 원칙 2가지**:
1. 유스케이스 코드에 `if hypervisor == "vcenter"` 같은 분기가 있어서는 안 됩니다. 하이퍼바이저별 차이는 전부 어댑터 내부에 캡슐화합니다.
2. 커넥터 Protocol과 어댑터에는 **조회 메서드만** 존재합니다. 자원을 변경하는 메서드는 정의 자체가 금지됩니다.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| API 서버 | FastAPI + uvicorn |
| vCenter 수집 | pyVmomi — PropertyCollector + ContainerView + RetrievePropertiesEx 페이징 |
| Hyper-V 수집 | pypsrp / WinRM (PowerShell Remoting), WMI `root\virtualization\v2`, 게스트 정보는 KVP |
| DB | PostgreSQL + SQLAlchemy (async) |
| 캐시 / 작업 큐 | Redis |
| 설정 | pydantic-settings |
| 인증·인가 | JWT + RBAC (조회 범위 제한) |
| 테스트 | pytest + pytest-asyncio |

## Key Constraints

- **하이퍼바이저 쓰기 금지** — 제어·변경 API를 호출하지 않습니다. 커넥터 Protocol과 어댑터에 조회 메서드만 정의하며, `scripts/arch_check.py`가 메서드명을 검사해 자동 차단합니다.
- **읽기 전용 계정** — 하이퍼바이저 접속 계정은 읽기 권한만 사용합니다 (vCenter: Read-Only 역할).
- **자격증명 보호** — 접속 정보는 암호화 저장하며, 로그·API 응답·예외 메시지·`__repr__`에 절대 노출하지 않습니다.
- **조회는 저장소 경유** — 사용자 조회 요청이 하이퍼바이저를 직접 호출하지 않습니다. 응답 성능과 하이퍼바이저 부하를 동시에 보호합니다.
- **부분 실패 허용** — 일부 연결이 장애여도 나머지 자원 조회는 정상 동작합니다. 실패한 연결의 데이터는 삭제하지 않고 신선도를 표시합니다.
- **자원 식별 일관성** — 재수집 시 동일 자원을 동일 레코드로 인식해야 합니다 (CI 식별 규칙: 연결ID+하이퍼바이저 고유ID → BIOS UUID → MAC+이름). 중복 레코드 생성은 결함입니다.
- **"값 없음"과 "수집 불가" 구분** — VMware Tools·통합 서비스 미설치 시 IP·게스트 OS를 얻을 수 없습니다. 빈 값으로 두지 말고 사유와 함께 수집 불가로 표시합니다.
- **포탈 메타데이터 보존** — 소유자·환경 등 포탈에서 입력한 값은 재수집이 덮어쓰지 않습니다.
- **수집 부하 제어** — 호출당 타임아웃(기본 60s), 재시도 최대 3회, 동시 연결 수 상한을 적용합니다.
- **조회 범위 제한** — 모든 조회는 요청자의 권한 범위(연결·자원 그룹)로 필터링합니다.
- **감사 로그** — 연결 등록·수정, 자격증명 변경, 메타데이터 변경, 리포트 내보내기를 기록합니다. 인벤토리 정보 자체가 민감 정보이므로 내보내기도 감사 대상입니다.

## Domain Model

관리 자원: **VirtualMachine**, **Host**, **Cluster**, **Datastore**, **Network**, **Snapshot**
(vCenter 전용: Datacenter/Folder, ResourcePool)

vCenter와 Hyper-V는 자원 모델이 다르므로(Cluster ↔ Failover Cluster, Datastore ↔ CSV/SMB, 포트그룹 ↔ 가상 스위치),
**어댑터가 하이퍼바이저 고유 모델을 공통 도메인 모델로 정규화**합니다.
속성별 수집 출처 대응표는 `spec.md` §2에 있으며, 정규화 매핑 규칙은 `docs/02_decision.md`에 결정으로 기록합니다.

## Multi-Agent Build System

`.claude/agents/` 디렉토리의 `.md` 파일로 에이전트를 정의하고, Claude Agent SDK로 실행합니다.

```
.claude/agents/
├── team-lead.md             # 오케스트레이터 (메인 에이전트)
├── requirements-analyst.md  # 요구사항 분석
├── research-planner.md      # 기술 조사 및 구현 계획
├── implementer.md           # 코드 구현
└── verifier.md              # 검증 및 테스트
agents/
└── run.py                   # 실행 스크립트
```

### 에이전트 구성 및 Phase

| Phase | Agent | 산출물 |
|-------|-------|--------|
| 1 | **requirements-analyst** | `docs/01_requirements.md` |
| 2 | **research-planner** | `plans/*.md` (영역별 계획서) |
| 3 | **implementer** | `src/`, `pyproject.toml` |
| 4 | **verifier** | `tests/`, `docs/verification_report.md` |

**team-lead**가 각 Phase의 산출물을 검토·승인한 후 다음 Phase로 진행합니다.

### 실행 방법

```bash
pip install claude-agent-sdk anyio
python -m agents.run              # 전체 (Phase 1~4)
python -m agents.run --phase 1    # 요구사항 분석만
python -m agents.run --phase 2    # +계획
python -m agents.run --phase 3    # +구현
```

## 아키텍처 규칙 검사

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 합니다.

```
domain → config/utils → infrastructure → application → orchestration → interface → entry
```

```bash
python scripts/arch_check.py              # 위반 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --ci         # CI 모드 (위반 시 exit 1)
```

일반 계층 규칙에 더해 이 프로젝트 특화 규칙 3개를 검사합니다:

1. `application`/`orchestration` → `infrastructure.vcenter|hyperv` **직접 import 금지** (Protocol 주입 필수)
2. 하이퍼바이저 어댑터 간 **교차 참조 금지** (`vcenter` ↔ `hyperv`)
3. **읽기 전용 범위 강제** — 커넥터 Protocol(`src/domain/ports.py`)과 어댑터의 public 메서드에 자원 변경 접두사(`create_`, `delete_`, `power_`, `migrate_` 등) 사용 금지

Claude Code 스킬: `/arch-check` 로 호출 가능 (`.claude/skills/arch-check.md`)

## 실수 방지 및 의사결정 관리

### 에이전트 실수 이력 관리

에이전트가 작업 중 실수한 항목은 `CLAUDE.md`의 아래 "Known Mistakes" 섹션에 기록하여 동일 실수가 반복되지 않도록 합니다.

- 실수 발생 시: 원인과 수정 내용을 즉시 기록
- 작업 시작 시: Known Mistakes 섹션을 확인하여 동일 패턴 재발 방지
- 형식: `[날짜] 실수 내용 — 원인 — 방지책`

### 의사결정 기록 (`docs/02_decision.md`)

프로젝트의 아키텍처·설계 의사결정은 `docs/02_decision.md`에 일원화하여 관리합니다.

**작업 전 (필수)**:
1. `docs/02_decision.md`를 읽고 기존 결정 사항을 확인합니다.
2. 수행할 작업이 기존 결정과 충돌하는지 검토합니다.
3. **충돌이 발견되면 임의로 진행하지 말고 사용자에게 문의**하여 결정을 받습니다.

**작업 후 (필수)**:
1. 작업 중 새로운 의사결정이 발생하면 `docs/02_decision.md`에 추가합니다.
2. 기존 결정이 변경되었으면 해당 항목의 상태를 갱신합니다.
3. 형식: 기존 `D-NNN` 번호 체계를 따릅니다 (결정일, 상태, 결정 내용, 근거, 대안).

---

## Known Mistakes (에이전트 실수 이력)

> 에이전트가 반복하지 말아야 할 실수 목록. 작업 시작 전 반드시 확인할 것.
> 아래 3건은 동일 기술 스택(Python + pydantic-settings + Clean Architecture)을 쓰는 collectorinfra 프로젝트에서 실제 발생한 사례를 이관한 것으로, 이 프로젝트에서도 동일하게 재발할 수 있습니다.

| 날짜 | 실수 | 원인 | 방지책 |
|------|------|------|--------|
| 2026-03-23 (이관) | `.env`의 `list[str]` 필드를 쉼표 구분 문자열로 설정하여 pydantic-settings 파싱 에러 발생 | pydantic-settings는 복합 타입(list, dict)을 JSON으로 파싱함 | `.env`에서 `list[str]` 필드는 반드시 JSON 배열 형식(`["a","b"]`)으로 작성 |
| 2026-03-23 (이관) | 유틸 함수를 상위 계층에 배치하여 역방향 의존(infrastructure→application) 발생 | 함수의 계층 소속을 고려하지 않고 사용처 옆에 배치 | 새 함수 작성 시 `python scripts/arch_check.py` 로 계층 위반 검사 후 배치. 데이터 모델 변환 함수는 해당 모델이 있는 계층에 위치 |
| 2026-06-10 (이관) | `model_post_init`에서 `os.getenv()`로 환경변수를 읽어 systemd 서비스(EnvironmentFile 미설정)에서 값이 로드되지 않음 | pydantic-settings의 `env_file` 로딩은 `os.environ`에 주입하지 않아 `os.getenv()`로 접근 불가 | `model_post_init`에서 `os.getenv()` 대신 pydantic-settings `AliasChoices`를 사용하여 `.env` 파일에서 직접 읽도록 구현 |
