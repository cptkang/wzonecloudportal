# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

vCenter 및 Hyper-V 관리 콘솔을 연결하여 각 하이퍼바이저에 생성된 가상자원(VM, 호스트, 클러스터, 데이터스토어, 네트워크)을 단일 화면에서 조회·제어·프로비저닝하는 **클라우드 포탈**.

전체 요건은 `spec.md`에 있습니다.
아키텍처 의사결정과 근거는 `docs/02_decision.md`에 기록합니다 — **team-lead 에이전트는 변경 전 이 파일을 반드시 확인하고, 새 결정이 생기면 갱신해야 합니다.**

## Architecture

**하이퍼바이저 어댑터 패턴** — 도메인이 정의한 포트(Protocol)를 각 하이퍼바이저 어댑터가 구현합니다.

```
        ┌──────────────── src/api (FastAPI) ────────────────┐
        │                                                    │
        ▼                                                    ▼
 src/application (유스케이스)              src/orchestration (워커/스케줄러)
        │                                                    │
        └───────────► src/domain/ports ◄─────────────────────┘
                   HypervisorConnector (Protocol)
                              ▲
              ┌───────────────┴───────────────┐
   src/infrastructure/vcenter        src/infrastructure/hyperv
        (pyVmomi)                      (WinRM / PowerShell)
```

**핵심 원칙**: 유스케이스 코드에는 `if hypervisor == "vcenter"` 같은 분기가 존재해서는 안 됩니다. 하이퍼바이저별 차이는 전부 어댑터 내부에 캡슐화합니다.

**데이터 흐름**:
- **조회**: 인벤토리 캐시(DB/Redis) → API 응답. 하이퍼바이저를 직접 호출하지 않습니다.
- **동기화**: `orchestration` 워커가 주기적으로 하이퍼바이저를 폴링 → 인벤토리 캐시 갱신.
- **변경**: API → 유스케이스 → 커넥터 → 하이퍼바이저. 비동기 Task로 등록하고 상태를 추적합니다.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| API 서버 | FastAPI + uvicorn |
| vCenter 연동 | pyVmomi (vSphere Automation SDK 병용 가능) |
| Hyper-V 연동 | pypsrp / WinRM (PowerShell Remoting) |
| DB | PostgreSQL + SQLAlchemy (async) |
| 캐시 / 작업 큐 | Redis |
| 설정 | pydantic-settings |
| 인증·인가 | JWT + RBAC (테넌트 스코프) |
| 테스트 | pytest + pytest-asyncio |

## Key Constraints

- **자격증명 보호** — 하이퍼바이저 접속 정보는 암호화 저장하며, 로그·API 응답·에러 메시지·예외 트레이스에 절대 노출하지 않습니다.
- **파괴적 작업 가드** — VM 삭제, 강제 전원 오프, 스냅샷 복원, 디스크 분리는 명시적 확인 파라미터를 요구하고 감사 로그를 남깁니다.
- **멱등성** — 모든 쓰기 작업은 Task ID 기반 멱등 처리로 재시도 시 중복 생성을 방지합니다.
- **테넌트 격리** — 모든 조회·변경은 요청자의 테넌트 스코프로 필터링합니다. 전역 조회는 관리자 역할만 허용합니다.
- **하이퍼바이저 API 보호** — 호출당 타임아웃(기본 60s), 재시도 최대 3회, 동시 호출 상한을 적용합니다. 목록 조회는 캐시를 경유합니다.
- **감사 로그** — 모든 상태 변경 작업은 `누가/언제/무엇을/어떤 하이퍼바이저에서/결과` 를 기록합니다.
- **읽기 우선 기본값** — 신규 API는 조회부터 구현하고, 변경 API는 권한·감사·멱등성이 갖춰진 뒤 추가합니다.

## Domain Model

포탈이 관리하는 자원 유형: **VM**(가상 머신), **Host**(하이퍼바이저 호스트), **Cluster**, **Datastore**(스토리지), **Network**(포트그룹/가상 스위치), **ResourcePool**, **Snapshot**, **Task**(비동기 작업).

vCenter와 Hyper-V는 자원 모델이 다르므로(예: vCenter의 Cluster ↔ Hyper-V의 Failover Cluster, 포트그룹 ↔ 가상 스위치), **어댑터가 하이퍼바이저 고유 모델을 도메인 모델로 정규화**합니다. 정규화 매핑 규칙은 `docs/02_decision.md`에 결정으로 기록합니다.

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

## Clean Architecture 계층 규칙

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 합니다.

```
domain → config/utils → infrastructure → application → orchestration → interface → entry
```

```bash
python scripts/arch_check.py              # 위반 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --ci         # CI 모드 (위반 시 exit 1)
```

일반 계층 규칙에 더해 이 프로젝트 특화 규칙 2개를 검사합니다:

1. `application`/`orchestration` → `infrastructure.vcenter|hyperv` **직접 import 금지** (Protocol 주입 필수)
2. 하이퍼바이저 어댑터 간 **교차 참조 금지** (`vcenter` ↔ `hyperv`)

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
