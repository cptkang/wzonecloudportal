# 04. vCenter 수집 어댑터

> Wave: 2
> 계층: infrastructure (`src/infrastructure/vcenter/`)
> 담당 요건: FR-203(대규모 수집), FR-106(연결 테스트), FR-301, `spec.md` §2.2 vCenter 출처 열
> 의존: 02, 03
> 관련 결정: D-003, D-005, D-007

## 1. 목적

pyVmomi로 vCenter 인벤토리를 수집하여 공통 도메인 모델로 반환한다.
`HypervisorInventoryReader` Protocol(계획 03)을 구현한다.

**05(Hyper-V 어댑터)를 절대 import하지 않는다.** arch-check 특화 규칙 7번 위반이다.
공통 로직이 필요하면 팀 리드에게 보고한다.

## 2. 모듈 구성

```
src/infrastructure/vcenter/
├── __init__.py          VCenterInventoryReader export
├── reader.py            Protocol 구현 (진입점)
├── session.py           연결·세션 관리
├── collector.py         PropertyCollector 조회 로직
├── property_specs.py    자원 유형별 수집 속성 정의
├── mapper.py            pyVmomi 객체 → 도메인 모델 변환
└── errors.py            pyVmomi 예외 → 도메인 예외 변환
```

## 3. PropertyCollector 기반 수집 (FR-203) — 이 계획의 핵심

`docs/00_research_notes.md` §4.2 참조. **자원을 하나씩 순회하며 개별 속성을 읽으면 안 된다.**
대규모 환경에서 왕복 횟수가 자원 수에 비례해 폭증한다.

### 3.1 구조

```
ContainerView(rootFolder, type=[vim.VirtualMachine], recursive=True)
  └ PropertyFilterSpec
      ├ ObjectSpec(obj=containerView, skip=True)
      ├ TraversalSpec(type=ContainerView, path="view", skip=False)
      └ PropertySpec(type=vim.VirtualMachine, pathSet=[...필요한 속성만...])
→ RetrievePropertiesEx(specSet, options=RetrieveOptions(maxObjects=BATCH))
→ 응답의 token이 있으면 ContinueRetrievePropertiesEx(token) 반복
→ token이 없으면 종료
```

### 3.2 수집 속성 목록 (`property_specs.py`)

**필요한 속성만 명시한다.** `pathSet`을 비우면 전체 속성을 가져와 응답이 거대해진다.

```python
VM_PROPERTIES = [
    "name", "config.uuid", "config.instanceUuid",
    "config.version", "config.firmware", "config.guestFullName",
    "config.annotation", "config.createDate",
    "config.hardware.numCPU", "config.hardware.numCoresPerSocket",
    "config.hardware.memoryMB", "config.hardware.device",
    "runtime.powerState", "runtime.connectionState", "runtime.host", "runtime.bootTime",
    "guest.guestFullName", "guest.guestFamily", "guest.hostName",
    "guest.net", "guest.toolsStatus", "guest.toolsRunningStatus", "guest.toolsVersion",
    "snapshot", "resourcePool", "parent",
]
HOST_PROPERTIES = [
    "name", "runtime.connectionState", "runtime.inMaintenanceMode", "runtime.bootTime",
    "hardware.systemInfo.vendor", "hardware.systemInfo.model",
    "hardware.systemInfo.serialNumber",     # 서비스 태그
    "hardware.cpuInfo.numCpuPackages", "hardware.cpuInfo.numCpuCores", "hardware.cpuInfo.hz",
    "hardware.cpuPkg", "hardware.memorySize",
    "config.product.name", "config.product.version", "config.product.build",
    "config.network.pnic", "parent", "vm",
]
# CLUSTER_PROPERTIES, DATASTORE_PROPERTIES, NETWORK_PROPERTIES 동일 방식
```

> **검증 필요** (`docs/00_research_notes.md` §11-2): 속성 경로는 vSphere 버전에 따라 존재 여부가 다를 수 있다.
> 대상 vCenter 버전에서 실제 조회하여 확인한 뒤 확정한다. 누락 속성은 예외가 아닌 `None`으로 처리한다.

### 3.3 비동기 처리

pyVmomi는 동기 라이브러리다. 이벤트 루프를 막지 않도록 오프로드한다.

```python
async def _retrieve_page(self, spec, token=None):
    return await asyncio.to_thread(self._retrieve_page_sync, spec, token)
```

`AsyncIterator`로 페이지 단위 yield하여 메모리 점유를 제한한다 (계획 03 §2.1).

## 4. 세션 관리 (`session.py`)

```python
async def start_session(self) -> None:
    """SmartConnect로 세션을 연다. 자격증명은 이 시점에만 복호화한다."""

async def close_session(self) -> None:
    """Disconnect로 세션을 닫는다. 미호출 시 vCenter에 세션이 누적된다."""
```

- **세션 누수 주의**: vCenter는 유휴 세션을 일정 시간 유지하므로, 수집 실패 시에도 반드시 `close_session`을 호출한다. `try/finally` 또는 async context manager로 보장한다.
- TLS 인증서 검증은 연결 설정(FR-115)에 따라 분기한다. 검증 비활성 시 `ssl._create_unverified_context()`를 쓰되, **기본값은 검증 활성**이다.
- 자격증명은 `SecretStr`로 받아 `get_secret_value()`를 `SmartConnect` 호출 인자에서만 사용한다 (계획 10 §2.3).

## 5. 게스트 정보 매핑 (FR-501) — 주의 지점

`docs/00_research_notes.md` §6: VMware Tools가 없으면 IP·게스트 OS·호스트명을 얻을 수 없다.

```python
def map_guest_info(props: dict) -> GuestInfo:
    tools_status = props.get("guest.toolsStatus")
    running = props.get("guest.toolsRunningStatus")

    if tools_status == "toolsNotInstalled":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)
    if running != "guestToolsRunning":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)
    if not props.get("guest.hostName") and not props.get("guest.net"):
        return GuestInfo(availability=GuestInfoAvailability.UNKNOWN)

    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=props.get("guest.guestFullName"),
        hostname=props.get("guest.hostName"),
        ipv4_addresses=..., ipv6_addresses=...,
        tool_version=props.get("guest.toolsVersion"),
    )
```

**`guest.guestFullName`(도구 감지값)이 없으면 `config.guestFullName`(구성값)으로 폴백하되, `os_source`를 `VM_CONFIG`로 표시한다** (FR-304).

주의사항:
- `guest.net[].ipAddress`에는 링크로컬(`fe80::`), 루프백이 포함될 수 있다. **필터링 규칙을 `src/utils/net.py`에 두고 어댑터가 사용한다.**
- Tools 상태가 `toolsOk`인데 IP가 비어 있는 경우가 있다(부팅 직후). `UNKNOWN`으로 처리한다.
- Tools 설치 여부 보고가 부정확한 알려진 버그가 있다(§6.3). 상태를 단정하지 말고 값 유무도 함께 확인한다.

## 6. 하드웨어 장치 매핑

`config.hardware.device`는 모든 가상 장치가 섞인 배열이다. 타입으로 분류한다.

| 도메인 모델 | pyVmomi 타입 | 매핑 |
|---|---|---|
| `VirtualDisk` | `vim.vm.device.VirtualDisk` | `capacityInKB` → 바이트, `backing.thinProvisioned` → Thin 여부, `backing.fileName` → 데이터스토어 경로 |
| `NetworkAdapter` | `vim.vm.device.VirtualEthernetCard` 하위 | `macAddress`, `deviceInfo.summary` → 포트그룹명, 클래스명 → 어댑터 타입 |

**디스크 실제 사용량**은 `capacityInKB`(프로비저닝)와 다르다. Thin 디스크의 실제 사용량은 별도 경로가 필요하므로,
1차 구현에서는 프로비저닝 용량만 채우고 실제 사용량은 `None`으로 둔다. 확보 방법은 `[TODO]`로 남긴다.

## 7. 연결 테스트 (FR-106)

계획 03 §4의 4단계를 구현한다.

| 단계 | 방법 |
|---|---|
| REACHABLE | TCP 연결 시도 (짧은 타임아웃) |
| TLS_VALID | 인증서 검증 활성 시 핸드셰이크 성공 여부 |
| AUTHENTICATED | `SmartConnect` 성공 |
| AUTHORIZED | 자원 유형별로 **1건만** 조회 시도 (`maxObjects=1`) |

권한이 없으면 pyVmomi가 `vim.fault.NoPermission`을 던진다. 이를 해당 유형만 실패로 기록하고 나머지는 계속 확인한다.

## 8. 예외 변환 (`errors.py`)

**pyVmomi 예외가 어댑터 밖으로 나가면 안 된다** (계층 규약, `plans/README.md` §3.5).

| pyVmomi 예외 | 도메인 예외 | retryable |
|---|---|---|
| `vim.fault.InvalidLogin` | `AuthenticationError` | **False** |
| `vim.fault.NoPermission` | `PermissionError` | False |
| `vim.fault.HostConnectFault`, 소켓 오류, 타임아웃 | `UnreachableError` | True |
| SSL 인증서 오류 | `UnreachableError` (사유 명시) | True |
| 기타 `vmodl.MethodFault` | `CollectionError` | 상황별 |

**`InvalidLogin` → `AuthenticationError` 매핑이 계정 잠금 방지의 출발점이다** (FR-114, CST-05).
예외 메시지를 그대로 전달하지 말고 자격증명이 포함되지 않도록 정제한다 (계획 10 §2.4).

## 9. Capability

```python
ReaderCapabilities(
    kind=HypervisorKind.VCENTER,
    supports_resource_pool=True,
    supports_folder_hierarchy=True,
    supports_native_tags=False,      # §11-7 검증 후 조정 — Automation SDK 필요 가능성
    supports_cluster=True,
    supports_incremental=False,      # Phase 1은 전량 수집
    collectable_types=frozenset(ResourceType),
)
```

**vSphere Tags(FR-606)**: pyVmomi만으로는 조회할 수 없고 vSphere Automation SDK(REST)가 필요할 가능성이 있다.
검증 전까지 `supports_native_tags=False`로 두고 수집하지 않는다 (`docs/00_research_notes.md` §11-7).

## 10. 구현 순서

1. `errors.py` 예외 변환 → 검증: `InvalidLogin` → `AuthenticationError(retryable=False)`
2. `session.py` → 검증: 연결/해제, 실패 시에도 세션 정리
3. `property_specs.py` → 검증: 대상 vCenter에서 모든 속성 경로가 조회되는지 실측
4. `collector.py` 페이징 → 검증: `maxObjects`보다 많은 자원에서 토큰 반복 동작
5. `mapper.py` — VM → 검증: 필수 속성 매핑, 게스트 정보 상태별 분기
6. `mapper.py` — Host/Cluster/Datastore/Network
7. `reader.py` Protocol 구현 → 검증: **계약 테스트 스위트(계획 03) 통과**
8. 연결 테스트 → 검증: 단계별 결과 반환

## 11. 완료 기준

- [ ] `arch_check.py` 통과 — hyperv 미참조, **읽기 전용 메서드만 존재**
- [ ] 계약 테스트 스위트 통과 (05와 동일 스위트)
- [ ] PropertyCollector 페이징으로 `maxObjects` 초과 자원 전량 수집
- [ ] Tools 미설치 VM이 `TOOLS_NOT_INSTALLED`로 매핑됨
- [ ] pyVmomi 예외가 어댑터 밖으로 나오지 않음
- [ ] 세션이 실패 경로에서도 해제됨
- [ ] 수집 중 vCenter에 쓰기 API 호출이 없음 (코드 리뷰로 확인 — arch-check로는 못 잡음, D-005 한계)

## 12. 주의사항

- **`Destroy_Task`, `PowerOffVM_Task`, `ReconfigVM_Task` 등 조작 API를 호출하지 않는다.** 메서드명 검사로는 잡히지 않으므로 verifier가 코드에서 직접 확인한다.
- `ContainerView`는 사용 후 `DestroyView()`로 정리해야 vCenter 측 자원이 해제된다. **이는 뷰 객체 정리이지 자원 삭제가 아니다** — 읽기 전용 원칙에 위배되지 않으나, 메서드명 오해를 피하려면 래퍼 함수명을 `release_view`로 둔다.
- 대규모 환경에서 `maxObjects`를 너무 크게 잡으면 응답 크기가 커져 타임아웃이 난다. 기본 500에서 시작해 조정한다.
- 개발·테스트는 목 커넥터로 한다 (CST-04). 운영 vCenter에 붙지 않는다.
