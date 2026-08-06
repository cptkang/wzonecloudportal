---
name: arch-check
description: 계층 의존성 위반, 하이퍼바이저 어댑터 결합, 읽기 전용 범위 위반을 자동 탐지하고 수정 방안을 제시한다
user_invocable: true
---

# 아키텍처 규칙 검사 스킬

## 개요

`src/` 디렉토리를 분석하여 두 종류의 규칙 위반을 탐지한다.

1. **계층 의존성** — Clean Architecture 의존성 방향 (일반 규칙 + 하이퍼바이저 추상화 보호 규칙 2개)
2. **읽기 전용 범위** — 커넥터 Protocol·어댑터에 자원 변경 메서드가 정의되지 않았는지 (`spec.md` CST-01 / NFR-202)

## 계층 구조 (안쪽 → 바깥쪽)

```
domain (src/domain/)               — 자원 엔티티(VM/Host/Cluster/Datastore/Network),
                                     연결 정보, 메타데이터, 변경 이력, 포트(Protocol). 의존 없음
  ↑
config (src/config.py)             — 설정. 의존 없음
utils (src/utils/)                 — 공유 유틸. 의존 없음
  ↑
infrastructure (src/infrastructure/)  — 하이퍼바이저 수집 어댑터, DB, 캐시, 보안, 저장소
  (vcenter/, hyperv/, db/, cache/, security/, repository/)
  → domain, config, utils, infrastructure(같은 레벨) 참조 가능
  ↑
application (src/application/)     — 유스케이스 (인벤토리 조회·검색, 정규화, 메타데이터 관리, 리포트)
  → domain, config, utils, infrastructure 참조 가능
  ↑
orchestration (src/orchestration/) — 수집 스케줄러/워커
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
3. **infrastructure → application / orchestration / interface**: 인프라가 상위 계층을 참조하면 안 됨
4. **application → orchestration / interface**: 유스케이스가 워커·API를 참조하면 안 됨
5. **orchestration → interface**: 워커가 API를 참조하면 안 됨

### 특화 규칙 A — 하이퍼바이저 추상화 보호

6. **application / orchestration → `src.infrastructure.vcenter` 또는 `src.infrastructure.hyperv` 직접 import 금지**
   유스케이스가 특정 하이퍼바이저 구현에 결합되면 멀티 하이퍼바이저 지원이 무너진다.
   `src.domain.ports`의 커넥터 Protocol을 통해 주입받아야 한다.

7. **하이퍼바이저 어댑터 간 교차 참조 금지 (`vcenter` ↔ `hyperv`)**
   한쪽 어댑터가 다른 어댑터를 참조하면 두 구현이 서로 묶여 개별 교체가 불가능해진다.
   공통 로직은 `src/domain/` 또는 `src/utils/`로 추출한다.

### 특화 규칙 B — 읽기 전용 범위 강제

8. **커넥터 Protocol(`src/domain/ports.py`)과 하이퍼바이저 어댑터의 public 메서드에 자원 변경 접두사 사용 금지**

   이 포탈은 하이퍼바이저에 어떠한 쓰기·제어 API도 호출하지 않는다(`spec.md` §1.2, CST-01, NFR-202).
   문서 약속에 그치지 않도록 메서드명을 AST로 검사하여 강제한다.

   금지 접두사:
   ```
   create_  delete_  destroy_  remove_
   power_   start_   stop_     restart_  reboot_
   suspend_ resume_  reset_    shutdown_
   modify_  reconfigure_  resize_  rename_
   migrate_ relocate_  clone_   deploy_   provision_
   revert_  attach_   detach_   mount_    unmount_
   ```

   허용 예외 (하이퍼바이저 자원을 변경하지 않는 세션·수집 제어):
   ```
   start_session  stop_session  close_session  reset_session
   start_collection  stop_collection
   reset_connection  remove_stale_cache
   ```

   언더스코어로 시작하는 private 메서드는 검사 대상이 아니다.
   조회 목적이라면 `get_`, `list_`, `fetch_`, `collect_`, `read_`, `search_`, `count_` 등으로 명명한다.

## 실행 방법

### 자동화 스크립트

```bash
python scripts/arch_check.py              # 기본 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --json       # JSON 출력 (CI 연동)
python scripts/arch_check.py --ci         # 위반 시 exit 1
```

Windows 콘솔에서 한글이 깨지면 `chcp 65001` 실행 후 재시도한다.
`--verbose` 매트릭스는 **계층 단위 집계**이므로 특화 규칙 위반은 초록으로 표시될 수 있다.
개별 판정은 항상 ERRORS 목록을 기준으로 한다. JSON 출력의 `kind` 필드로 `dependency` / `readonly` 를 구분할 수 있다.

### 이 스킬 호출 시 수행 절차

1. `python scripts/arch_check.py --verbose` 실행하여 위반 목록 수집
2. 각 위반에 대해 해당 파일의 코드를 직접 확인
3. 위반 유형별 수정 방안 제시 (아래 패턴 참조)
4. 수정 적용 후 재검사하여 위반 해소 확인

## 수정 패턴

### 패턴 A: 함수를 올바른 계층으로 이동

위반: `infrastructure`가 `application`의 유틸 함수를 import
수정: 해당 함수를 `utils/` 또는 같은 `infrastructure` 모듈로 이동

### 패턴 B: 의존성 역전 (DIP) — 하이퍼바이저 어댑터 결합 해소

위반: `application`이 `src.infrastructure.vcenter.client.VCenterReader`를 직접 import

```python
# 잘못된 예 — 유스케이스가 vCenter 구현에 결합
from src.infrastructure.vcenter.client import VCenterReader

async def collect_inventory(connection) -> list[VirtualMachine]:
    reader = VCenterReader(...)
    return await reader.list_vms()
```

수정: `domain/ports.py`에 Protocol을 정의하고 주입받는다.

```python
# src/domain/ports.py (domain 계층 — 어떤 구현도 import 하지 않음)
from typing import Protocol

class HypervisorInventoryReader(Protocol):
    async def list_vms(self) -> list[VirtualMachine]: ...
    async def list_hosts(self) -> list[Host]: ...
    async def list_datastores(self) -> list[Datastore]: ...

# src/application/inventory.py (유스케이스 — Protocol에만 의존)
from src.domain.ports import HypervisorInventoryReader

async def collect_inventory(reader: HypervisorInventoryReader) -> list[VirtualMachine]:
    return await reader.list_vms()
```

구체 어댑터 선택은 `interface`/`entry` 계층의 팩토리(DI 컨테이너)에서 수행한다.

### 패턴 C: 어댑터 공통 로직 추출

위반: `hyperv` 어댑터가 `vcenter` 어댑터의 변환 함수를 import
수정: 공통 변환 로직을 `src/domain/` (도메인 규칙인 경우) 또는 `src/utils/` (순수 유틸인 경우)로 이동하여 양쪽 어댑터가 각각 참조하도록 한다

### 패턴 D: 읽기 전용 위반 해소

위반: 커넥터 Protocol 또는 어댑터에 자원 변경 메서드가 정의됨

```python
# 잘못된 예
class HypervisorInventoryReader(Protocol):
    async def power_off(self, vm_id: str) -> None: ...      # ✗ 제어 메서드
    async def delete_snapshot(self, snap_id: str) -> None: ...  # ✗ 변경 메서드
```

수정 판단:
- **조회 기능인데 이름이 잘못된 경우** → 조회 동사로 개명 (`remove_stale_records` → `list_stale_records` 후 정리는 저장소 계층에서 수행)
- **실제로 자원을 변경하려는 경우** → **구현하지 않는다.** 이 포탈의 명시적 비목표이므로 팀 리드에게 보고하고 사용자 범위 확인을 받는다
- **저장소·캐시 조작인데 어댑터에 있는 경우** → `infrastructure/repository` 또는 `infrastructure/cache`로 이동 (읽기 전용 검사 대상이 아님)

### 패턴 E: 콜백 주입

위반: 하위 계층이 상위 계층 로직에 의존
수정: 상위 계층에서 함수/콜백을 파라미터로 전달

## 새 모듈 추가 시

`src/`에 새 최상위 패키지를 만들면 `scripts/arch_check.py`의 `MODULE_LAYER_MAP`에 계층을 등록해야 한다.
등록하지 않은 모듈은 계층 미상으로 간주되어 **검사에서 조용히 제외**된다.

새 하이퍼바이저 어댑터(예: Proxmox, Nutanix)를 추가하는 경우
`MODULE_LAYER_MAP`과 `HYPERVISOR_ADAPTERS` 양쪽에 모두 등록한다.
`HYPERVISOR_ADAPTERS` 등록은 교차 참조 검사와 읽기 전용 검사 양쪽의 대상이 되게 하므로 누락하면 안 된다.
