# Decision Log

이 문서는 프로젝트의 주요 아키텍처 및 설계 의사결정을 기록합니다.
향후 요건 추가/수정 시 이 문서를 참고하여 의사결정의 방향성과 일관성을 유지합니다.

**작업 전**: 기존 결정(D-001 ~ 최신)을 읽고 충돌 여부를 검토합니다. 충돌 시 임의로 진행하지 말고 사용자에게 문의합니다.
**작업 후**: 새 결정은 `D-NNN` 번호를 부여하여 추가하고, 변경된 결정은 상태를 갱신합니다.

---

## 목차

1. [기술 스택: Python + FastAPI](#d-001-기술-스택-python--fastapi)
2. [아키텍처: Clean Architecture 8계층](#d-002-아키텍처-clean-architecture-8계층)
3. [하이퍼바이저 추상화: Protocol 기반 어댑터 패턴](#d-003-하이퍼바이저-추상화-protocol-기반-어댑터-패턴)
4. [개발 방식: 멀티 에이전트 빌드 시스템](#d-004-개발-방식-멀티-에이전트-빌드-시스템)

---

## D-001. 기술 스택: Python + FastAPI

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-08-06 |
| **상태** | 확정 |

### 결정

백엔드는 **Python + FastAPI**로 구현한다. vCenter는 pyVmomi, Hyper-V는 pypsrp/WinRM(PowerShell Remoting)으로 연동한다.

### 근거

- 동일 조직의 collectorinfra 프로젝트와 스택이 일치하여 인프라·운영 노하우와 Claude Code 구성요소를 재사용할 수 있음
- pyVmomi는 VMware가 관리하는 공식 Python SDK로 vSphere 기능 커버리지가 넓음
- Hyper-V는 네이티브 관리 인터페이스가 PowerShell이며, pypsrp로 원격 실행이 가능함
- `scripts/arch_check.py`(Python AST 기반 계층 검사)를 수정 없이 재사용 가능

### 고려한 대안

| 대안 | 장점 | 채택하지 않은 이유 |
|------|------|-------------------|
| C# / ASP.NET Core | Hyper-V/WMI 네이티브 접근, Windows 환경 친화 | 조직 내 기존 스택과 불일치, 아키텍처 검사 도구 재작성 필요 |
| Node.js / TypeScript | 프론트엔드와 언어 통일 | vCenter/Hyper-V용 성숙한 라이브러리 부족 |

---

## D-002. 아키텍처: Clean Architecture 8계층

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-08-06 |
| **상태** | 확정 |

### 결정

의존성 방향을 안쪽 → 바깥쪽으로 강제하는 8계층 구조를 채택하고, `scripts/arch_check.py`로 자동 검사한다.

```
domain → config/utils → infrastructure → application → orchestration → interface → entry
```

| 계층 | 경로 | 책임 |
|------|------|------|
| domain | `src/domain/` | 자원 엔티티, 테넌트/권한/Task 모델, 포트(Protocol) |
| config | `src/config.py` | 설정 |
| utils | `src/utils/` | 공유 유틸 |
| infrastructure | `src/infrastructure/` | 하이퍼바이저 어댑터, DB, 캐시, 보안, 리포지토리 |
| application | `src/application/` | 유스케이스 |
| orchestration | `src/orchestration/` | 인벤토리 동기화 워커, Task 실행기 |
| interface | `src/api/` | FastAPI 어댑터 |
| entry | `src/main.py` | 진입점 |

### 근거

- 하이퍼바이저 연동 코드(변경이 잦고 외부 의존이 큼)를 도메인 로직에서 분리하여 교체 가능하게 유지
- 계층 위반을 사람이 리뷰로 잡는 대신 스크립트로 자동 검출 (collectorinfra에서 효과 검증됨)
- 동기화 워커를 `orchestration`으로 분리하여 API 프로세스와 독립 배포 가능

### 고려한 대안

collectorinfra의 원본 계층에는 LangGraph 전용 계층(`prompts`, `nodes`)이 있었으나 이 프로젝트에는 LLM 파이프라인이 없어 제거했다.

---

## D-003. 하이퍼바이저 추상화: Protocol 기반 어댑터 패턴

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-08-06 |
| **상태** | 확정 |

### 결정

`src/domain/ports.py`에 `HypervisorConnector` Protocol을 정의하고, vCenter/Hyper-V 어댑터가 이를 구현한다.
유스케이스는 Protocol만 알고 구체 어댑터는 `interface`/`entry` 계층의 팩토리에서 주입한다.

이를 강제하기 위해 `scripts/arch_check.py`에 특화 규칙 2개를 구현했다:

1. `application`/`orchestration` → `src.infrastructure.vcenter|hyperv` **직접 import 금지** (error)
2. 하이퍼바이저 어댑터 간 **교차 참조 금지** (`vcenter` ↔ `hyperv`) (error)

### 근거

- 유스케이스에 `if hypervisor == "vcenter"` 분기가 퍼지면 하이퍼바이저 추가 시 전 계층을 수정해야 함
- 어댑터 간 교차 참조를 허용하면 두 구현이 묶여 개별 교체·제거가 불가능해짐
- 두 어댑터가 동일 Protocol 계약을 만족하므로 **동일 테스트 스위트로 계약 테스트**가 가능

### 미결 사항 (결정 필요)

- vCenter ↔ Hyper-V 자원 모델 정규화 매핑 규칙 (Cluster, 네트워크, 스토리지 개념 대응) → `plans/02-domain-model.md`에서 설계 후 D-005로 기록
- 한쪽만 지원하는 기능(예: ResourcePool)의 Protocol 표현 방식 — 미지원 예외 / capability 조회 / 선택적 메서드 → `plans/03-hypervisor-connector.md`에서 결정 후 기록

---

## D-004. 개발 방식: 멀티 에이전트 빌드 시스템

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-08-06 |
| **상태** | 확정 |

### 결정

collectorinfra 프로젝트의 Claude Code 멀티 에이전트 구성(team-lead + 4개 서브에이전트)을 이 프로젝트에 이관하여 사용한다.
품질 게이트는 `arch-check` → `/security-review` → `/code-review` → `/simplify` 순으로 적용한다.

### 근거

- Wave별 병렬 구현 + worktree 격리로 독립 모듈을 동시에 개발 가능
- 특히 **vCenter/Hyper-V 어댑터는 교차 참조가 금지되어 있어 병렬 구현에 구조적으로 적합**
- 자격증명·테넌트 격리·파괴적 작업이 핵심 위험이므로 `/security-review`를 필수 게이트로 승격
  (collectorinfra는 읽기 전용 DB 접근이라 이 게이트가 선택 사항이었음)

### 원본 대비 변경 사항

| 항목 | collectorinfra | 이 프로젝트 |
|------|---------------|------------|
| 핵심 보안 제약 | 읽기 전용 DB (DML/DDL 차단) | 자격증명 보호 + 파괴적 작업 가드 + 테넌트 격리 |
| 필수 품질 게이트 | arch-check, code-review | arch-check, **security-review**, code-review |
| Wave 구성 | state/db/security → nodes → graph | domain → 어댑터 → 유스케이스/워커 → API/UI |
| 스킬 카탈로그 | mcp-builder, frontend-design 등 | 현재 환경에 설치된 스킬만 (`plans/00-claude-skills-plugins.md` 참조) |
