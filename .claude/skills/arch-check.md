---
name: arch-check
description: Clean Architecture 계층 간 의존성 규칙 위반과 하이퍼바이저 어댑터 결합 위반을 자동 탐지하고 수정 방안을 제시한다
user_invocable: true
---

# Clean Architecture 의존성 규칙 검사 스킬

## 개요

이 프로젝트의 `src/` 디렉토리에 정의된 Clean Architecture 계층 구조를 분석하여 의존성 방향 위반을 탐지한다.
일반 계층 규칙에 더해, 멀티 하이퍼바이저(vCenter / Hyper-V) 추상화를 보호하는 프로젝트 특화 규칙 2개를 함께 검사한다.

## 계층 구조 (안쪽 → 바깥쪽)

```
domain (src/domain/)               — 자원 엔티티(VM/Host/Cluster/Datastore/Network),
                                     테넌트·권한·Task 모델, 포트(Protocol) 정의. 의존 없음
  ↑
config (src/config.py)             — 설정. 의존 없음
utils (src/utils/)                 — 공유 유틸. 의존 없음
  ↑
infrastructure (src/infrastructure/)  — 하이퍼바이저 어댑터, DB, 캐시, 보안, 리포지토리
  (vcenter/, hyperv/, db/, cache/, security/, repository/)
  → domain, config, utils, infrastructure(같은 레벨) 참조 가능
  ↑
application (src/application/)     — 유스케이스 (프로비저닝, 전원 제어, 인벤토리 조회, 할당량)
  → domain, config, utils, infrastructure 참조 가능
  ↑
orchestration (src/orchestration/) — 워커/스케줄러 (인벤토리 동기화, 비동기 Task 실행)
  → domain, config, utils, application, infrastructure 참조 가능
  ↑
interface (src/api/)               — FastAPI 어댑터
  → domain, config, utils, orchestration, application, infrastructure 참조 가능
  ↑
entry (src/main.py)                — 진입점
  → 모든 계층 참조 가능
```

## 핵심 금지 규칙

### 일반 계층 규칙

1. **domain → 모든 외부**: 도메인은 어디에도 의존하면 안 됨
2. **config / utils → 모든 내부 모듈**: 외부 패키지만 참조 가능
3. **infrastructure → application**: 인프라가 유스케이스를 참조하면 안 됨
4. **infrastructure → orchestration / interface**: 인프라가 워커·API를 참조하면 안 됨
5. **application → orchestration / interface**: 유스케이스가 워커·API를 참조하면 안 됨
6. **orchestration → interface**: 워커가 API를 참조하면 안 됨

### 프로젝트 특화 규칙 (하이퍼바이저 추상화 보호)

7. **application / orchestration → `src.infrastructure.vcenter` 또는 `src.infrastructure.hyperv` 직접 import 금지**
   유스케이스가 특정 하이퍼바이저 구현에 결합되면 멀티 하이퍼바이저 지원이 무너진다.
   `src.domain.ports`의 `HypervisorConnector` Protocol을 통해 주입받아야 한다.

8. **하이퍼바이저 어댑터 간 교차 참조 금지 (`vcenter` ↔ `hyperv`)**
   한쪽 어댑터가 다른 어댑터를 참조하면 두 구현이 서로 묶여 개별 교체가 불가능해진다.
   공통 로직은 `src/domain/` 또는 `src/utils/`로 추출한다.

## 실행 방법

### 자동화 스크립트

```bash
python scripts/arch_check.py              # 기본 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --json       # JSON 출력 (CI 연동)
python scripts/arch_check.py --ci         # 위반 시 exit 1
```

Windows 콘솔에서 한글이 깨지면 `chcp 65001` 실행 후 재시도한다.
`--verbose` 매트릭스는 **계층 단위 집계**이므로 특화 규칙 7·8번 위반은 초록으로 표시될 수 있다.
개별 위반 판정은 항상 ERRORS 목록을 기준으로 한다.

### 이 스킬 호출 시 수행 절차

1. `python scripts/arch_check.py --verbose` 실행하여 위반 목록 수집
2. 각 위반에 대해 해당 파일의 import 라인을 직접 확인
3. 위반 유형별 수정 방안 제시 (아래 패턴 참조)
4. 수정 적용 후 재검사하여 위반 해소 확인

## 일반적 수정 패턴

### 패턴 A: 함수를 올바른 계층으로 이동

위반: `infrastructure`가 `application`의 유틸 함수를 import
수정: 해당 함수를 `utils/` 또는 같은 `infrastructure` 모듈로 이동

### 패턴 B: 의존성 역전 (DIP) — 하이퍼바이저 어댑터 결합 해소

위반: `application`이 `src.infrastructure.vcenter.client.VCenterClient`를 직접 import

```python
# 잘못된 예 — 유스케이스가 vCenter 구현에 결합
from src.infrastructure.vcenter.client import VCenterClient

async def power_off_vm(vm_id: str) -> None:
    client = VCenterClient(...)
    await client.power_off(vm_id)
```

수정: `domain/ports.py`에 Protocol을 정의하고 유스케이스는 주입받는다.

```python
# src/domain/ports.py (domain 계층 — 어떤 구현도 import 하지 않음)
from typing import Protocol

class HypervisorConnector(Protocol):
    async def power_off(self, vm_id: str) -> None: ...
    async def list_vms(self) -> list[VirtualMachine]: ...

# src/application/power_control.py (유스케이스 — Protocol에만 의존)
from src.domain.ports import HypervisorConnector

async def power_off_vm(connector: HypervisorConnector, vm_id: str) -> None:
    await connector.power_off(vm_id)
```

구체 어댑터 선택은 `interface`/`entry` 계층의 팩토리(DI 컨테이너)에서 수행한다.

### 패턴 C: 어댑터 공통 로직 추출

위반: `hyperv` 어댑터가 `vcenter` 어댑터의 변환 함수를 import
수정: 공통 변환 로직을 `src/domain/` (도메인 규칙인 경우) 또는 `src/utils/` (순수 유틸인 경우)로 이동하여 양쪽 어댑터가 각각 참조하도록 한다

### 패턴 D: 콜백 주입

위반: 하위 계층이 상위 계층 로직에 의존
수정: 상위 계층에서 함수/콜백을 파라미터로 전달

## 새 모듈 추가 시

`src/`에 새 최상위 패키지를 만들면 `scripts/arch_check.py`의 `MODULE_LAYER_MAP`에 계층을 등록해야 한다.
등록하지 않은 모듈은 계층 미상으로 간주되어 **검사에서 조용히 제외**된다.

새 하이퍼바이저 어댑터(예: Proxmox, Nutanix)를 추가하는 경우
`MODULE_LAYER_MAP`과 `HYPERVISOR_ADAPTERS` 양쪽에 모두 등록한다.
