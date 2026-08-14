# 02. 도메인 모델 및 정규화 규칙

> Wave: 1 · 계층: domain (`src/domain/`)
> 담당 요건: FR-301, FR-302, FR-303, FR-304, FR-501, `spec.md` §2 속성 카탈로그
> 의존: 01 · 관련 결정: D-002, D-006

## 1. 목적과 범위

vCenter/Hyper-V의 상이한 자원 모델을 담을 공통 도메인 모델, CI 식별 규칙, 속성 출처 우선순위를 정의한다.
어댑터(04·05), 저장소(06), 유스케이스(07·12)가 모두 참조하는 계약이다.

**도메인은 어떤 내부 모듈도 import하지 않는다.** 외부 패키지는 표준 라이브러리와 `pydantic`만 허용한다.
SQLAlchemy·pyVmomi·FastAPI 타입이 들어오면 계층이 무너진다.

**Python 3.11 기준**이므로 PEP 695 제네릭 문법(`class Foo[T]`)을 쓸 수 없다. `typing.Generic`을 사용한다.

## 2. 파일 구성

| 파일 | 내용 |
|---|---|
| `enums.py` | 모든 열거형 |
| `exceptions.py` | 도메인 예외 계층 |
| `values.py` | 값 객체 (GuestInfo, CpuSpec, VirtualDisk, …) |
| `resource.py` | 자원 엔티티 (VirtualMachine, Host, …) |
| `connection.py` | 연결 정보 |
| `metadata.py` | 포탈 부여 메타데이터 |
| `history.py` | 변경 이력, 수집 이력 |
| `auth.py` | 사용자, 역할, 조회 범위 |
| `audit.py` | 감사 이벤트 |
| `identity.py` | CI 식별 키 생성 |
| `ports.py` | Protocol 정의 (계획 03) |

---

## 3. 열거형 (`enums.py`)

```python
from enum import StrEnum


class HypervisorKind(StrEnum):
    VCENTER = "vcenter"
    HYPERV = "hyperv"


class ConnectionKind(StrEnum):
    """연결 단위.

    Microsoft 환경은 관리 콘솔이 두 계열이라 수집 경로가 갈린다 (D-012, 계획 05 §2).
    - Hyper-V 관리자 계열: 중앙 관리 지점이 없어 호스트/클러스터 단위로 등록
    - SCVMM: 서버 1대가 fabric 전체를 보유하므로 vCenter와 같은 단위로 등록
    """
    VCENTER = "vcenter"
    HYPERV_HOST = "hyperv-host"          # 경로 A — 단독 호스트
    HYPERV_CLUSTER = "hyperv-cluster"    # 경로 A — 장애 조치 클러스터
    SCVMM = "scvmm"                      # 경로 B — SCVMM 관리 서버

    @property
    def hypervisor(self) -> HypervisorKind:
        return HypervisorKind.VCENTER if self is ConnectionKind.VCENTER else HypervisorKind.HYPERV


class WinRmAuth(StrEnum):
    NTLM = "ntlm"
    KERBEROS = "kerberos"
    CREDSSP = "credssp"                  # 자격증명 위임 — UI 경고 필요


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"                # 관리자가 비활성화 (FR-112)
    CREDENTIAL_ERROR = "credential_error" # 인증 실패 — 자동 재시도 중단 (FR-114)
    PERMISSION_ERROR = "permission_error"
    UNREACHABLE = "unreachable"


class ResourceType(StrEnum):
    VIRTUAL_MACHINE = "virtual_machine"
    HOST = "host"
    CLUSTER = "cluster"
    DATASTORE = "datastore"
    NETWORK = "network"
    SNAPSHOT = "snapshot"


class PowerState(StrEnum):
    """vCenter/Hyper-V 전원 상태를 4값으로 통일한다.

    vCenter: poweredOn / poweredOff / suspended
    Hyper-V: Running / Off / Saved / Paused  (Saved·Paused 모두 SUSPENDED)
    """
    ON = "on"
    OFF = "off"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class ConnectionState(StrEnum):
    """자원 자체의 연결 상태 (하이퍼바이저가 자원을 인지하는 상태)."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    INACCESSIBLE = "inaccessible"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"


class GuestInfoAvailability(StrEnum):
    """게스트 정보 수집 가능 여부 (FR-501).

    VMware Tools / Hyper-V 통합 서비스가 동작해야 게스트 OS·IP·FQDN을 얻을 수 있다.
    """
    AVAILABLE = "available"
    TOOLS_NOT_INSTALLED = "tools_not_installed"
    TOOLS_NOT_RUNNING = "tools_not_running"
    UNKNOWN = "unknown"


class OsSource(StrEnum):
    """게스트 OS 값의 출처 (FR-304)."""
    GUEST_TOOLS = "guest_tools"          # 권위 있는 값
    VM_CONFIG = "vm_config"              # 대체값 — 실제와 다를 수 있음


class Firmware(StrEnum):
    BIOS = "bios"
    UEFI = "uefi"
    UNKNOWN = "unknown"


class DiskProvisioning(StrEnum):
    THIN = "thin"                        # vCenter Thin / Hyper-V 동적 확장
    THICK = "thick"                      # vCenter Thick / Hyper-V 고정
    UNKNOWN = "unknown"


class NetworkKind(StrEnum):
    """원본 유형을 보존한다 (NFR-402 원본 용어 병기)."""
    STANDARD_PORTGROUP = "standard_portgroup"
    DISTRIBUTED_PORTGROUP = "distributed_portgroup"
    VSWITCH_EXTERNAL = "vswitch_external"
    VSWITCH_INTERNAL = "vswitch_internal"
    VSWITCH_PRIVATE = "vswitch_private"
    UNKNOWN = "unknown"


class DatastoreKind(StrEnum):
    VMFS = "vmfs"
    NFS = "nfs"
    VSAN = "vsan"
    CSV = "csv"                          # Hyper-V Cluster Shared Volume
    SMB = "smb"
    LOCAL = "local"
    UNKNOWN = "unknown"


class ResourceLifecycle(StrEnum):
    ACTIVE = "active"
    MISSING = "missing"                  # 수집에서 사라짐 — 유예 중 (FR-307)
    RETIRED = "retired"                  # 유예 경과 후 폐기
    DISCONNECTED = "disconnected"        # 연결 삭제됨 (FR-109)


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    TEST = "test"


class Criticality(StrEnum):
    TIER_0 = "tier_0"
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
```

---

## 4. 도메인 예외 (`exceptions.py`)

`retryable` 속성이 계정 잠금 방지(FR-114, CST-05)의 핵심이다. 재시도 데코레이터가 이 값만 보고 분기한다.

```python
class PortalError(Exception):
    """모든 도메인 예외의 기반."""

    def __init__(self, message: str, *, detail: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ConnectionError(PortalError):
    """하이퍼바이저 연결 관련 오류."""
    retryable: bool = True


class AuthenticationError(ConnectionError):
    """인증 실패.

    재시도하면 AD 계정 잠금 정책에 걸려 서비스 계정이 잠기고,
    그 계정을 쓰는 다른 시스템까지 연쇄 장애가 발생한다 (CST-05).
    """
    retryable = False


class PermissionError(ConnectionError):
    """권한 부족. 자격증명은 유효하므로 재시도해도 결과가 같다."""
    retryable = False


class UnreachableError(ConnectionError):
    """네트워크·서버 오류. 일시적일 수 있으므로 재시도 대상."""
    retryable = True


class CollectionError(PortalError):
    """수집 중 오류 (파싱 실패, 예상치 못한 응답 등)."""

    def __init__(self, message: str, *, resource_type: ResourceType | None = None, **kw) -> None:
        super().__init__(message, **kw)
        self.resource_type = resource_type


class NotFoundError(PortalError): ...


class ValidationError(PortalError):
    """입력 검증 실패. API에서 422로 매핑된다."""

    def __init__(self, message: str, *, field: str | None = None, **kw) -> None:
        super().__init__(message, **kw)
        self.field = field


class DuplicateError(PortalError):
    """중복 등록 (FR-105). API에서 409로 매핑된다."""
```

### 4.1 재시도 유틸 (`src/utils/retry.py`)

도메인이 아닌 utils에 둔다. 도메인 예외를 import하면 utils→domain 의존이 생기므로,
**`retryable` 속성을 덕 타이핑으로 확인**한다.

```python
async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """지수 백오프 재시도. retryable=False 예외는 즉시 전파한다."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if not getattr(exc, "retryable", False):
                raise                                  # 인증 실패 등 — 즉시 중단
            last_exc = exc
            if attempt >= max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
```

> `getattr(exc, "retryable", False)`의 기본값이 `False`인 것이 중요하다.
> 알 수 없는 예외를 재시도하지 않는 쪽이 안전하다.

---

## 5. 값 객체 (`values.py`)

모두 `frozen=True` 불변 dataclass로 정의한다.

### 5.1 GuestInfo — 이 계획의 핵심 설계

`docs/00_research_notes.md` §6: 도구가 없으면 OS·IP·FQDN이 **함께** 없어진다. 따라서 묶어서 상태를 부여한다.

```python
@dataclass(frozen=True, slots=True)
class GuestInfo:
    """게스트 OS가 보고하는 정보.

    VMware Tools / Hyper-V 통합 서비스가 동작할 때만 수집된다.
    수집 불가 시에도 마지막으로 알려진 값을 보존한다 (계획 12 §2.4).
    """
    availability: GuestInfoAvailability
    os_name: str | None = None
    os_version: str | None = None
    os_source: OsSource | None = None
    hostname: str | None = None
    ipv4_addresses: tuple[str, ...] = ()
    ipv6_addresses: tuple[str, ...] = ()
    tool_version: str | None = None
    observed_at: datetime | None = None      # 이 값이 수집된 시각

    @property
    def is_collected(self) -> bool:
        return self.availability is GuestInfoAvailability.AVAILABLE

    @property
    def primary_ipv4(self) -> str | None:
        return self.ipv4_addresses[0] if self.ipv4_addresses else None

    def with_fallback(self, previous: "GuestInfo | None") -> "GuestInfo":
        """수집 불가 시 이전 값을 유지한 새 인스턴스를 반환한다.

        도구가 멈춘 것이지 VM의 IP가 없어진 것이 아니다.
        마지막으로 알려진 IP는 장애 대응에 유용하므로 보존한다.
        """
        if self.is_collected or previous is None:
            return self
        return replace(
            self,
            os_name=previous.os_name,
            os_version=previous.os_version,
            os_source=previous.os_source,
            hostname=previous.hostname,
            ipv4_addresses=previous.ipv4_addresses,
            ipv6_addresses=previous.ipv6_addresses,
            tool_version=previous.tool_version,
            observed_at=previous.observed_at,        # 원래 관측 시각 유지
        )


UNAVAILABLE_REASONS: dict[GuestInfoAvailability, str] = {
    GuestInfoAvailability.TOOLS_NOT_INSTALLED: "게스트 도구 미설치",
    GuestInfoAvailability.TOOLS_NOT_RUNNING: "게스트 도구 미동작",
    GuestInfoAvailability.UNKNOWN: "확인 필요",
}
```

`UNAVAILABLE_REASONS`를 도메인에 두어 UI(계획 11)·내보내기(계획 13)가 **동일한 문구**를 쓰게 한다.

### 5.2 스펙 값 객체

```python
@dataclass(frozen=True, slots=True)
class CpuSpec:
    total_vcpu: int
    socket_count: int | None = None
    cores_per_socket: int | None = None
    reservation_mhz: int | None = None
    limit_mhz: int | None = None


@dataclass(frozen=True, slots=True)
class MemorySpec:
    assigned_mb: int
    dynamic_enabled: bool = False        # Hyper-V 동적 메모리
    dynamic_min_mb: int | None = None
    dynamic_max_mb: int | None = None
    reservation_mb: int | None = None


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    hardware_version: str | None = None  # vCenter vmx-19 / Hyper-V 구성 버전
    firmware: Firmware = Firmware.UNKNOWN
    configured_os: str | None = None     # VM 구성값 OS (도구 없이도 조회 가능)
    generation: int | None = None        # Hyper-V 1 / 2


@dataclass(frozen=True, slots=True)
class VirtualDisk:
    key: str                             # 하이퍼바이저 내 장치 키
    label: str | None
    provisioned_bytes: int
    used_bytes: int | None               # Thin 실제 사용량 — 미확보 시 None
    provisioning: DiskProvisioning
    datastore_name: str | None
    file_path: str | None


@dataclass(frozen=True, slots=True)
class NetworkAdapter:
    key: str
    mac_address: str | None              # 정규화된 형식 (소문자, 콜론 구분)
    adapter_type: str | None             # vmxnet3, e1000, Synthetic 등
    network_name: str | None             # 포트그룹 / 가상 스위치
    connected: bool | None


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    count: int = 0
    latest_created_at: datetime | None = None
    total_size_bytes: int | None = None

    @property
    def age_days(self) -> int | None:
        if self.latest_created_at is None:
            return None
        return (datetime.now(UTC) - self.latest_created_at).days
```

---

## 6. 자원 엔티티 (`resource.py`)

### 6.1 공통 기반

```python
@dataclass
class ResourceBase:
    resource_id: UUID                    # 포탈 생성 ID (불변)
    connection_id: UUID                  # 소속 연결 (불변 — FR-110)
    native_id: str                       # 하이퍼바이저 고유 ID
    name: str
    lifecycle: ResourceLifecycle = ResourceLifecycle.ACTIVE
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    missing_since: datetime | None = None

    def is_stale(self, threshold: timedelta, now: datetime) -> bool:
        """데이터 신선도 판정 (FR-502)."""
        if self.last_seen_at is None:
            return True
        return (now - self.last_seen_at) > threshold
```

### 6.2 VirtualMachine

```python
@dataclass
class VirtualMachine(ResourceBase):
    bios_uuid: str | None = None
    power_state: PowerState = PowerState.UNKNOWN
    connection_state: ConnectionState = ConnectionState.UNKNOWN
    boot_time: datetime | None = None

    cpu: CpuSpec = field(default_factory=lambda: CpuSpec(total_vcpu=0))
    memory: MemorySpec = field(default_factory=lambda: MemorySpec(assigned_mb=0))
    platform: PlatformSpec = field(default_factory=PlatformSpec)
    guest: GuestInfo = field(
        default_factory=lambda: GuestInfo(availability=GuestInfoAvailability.UNKNOWN)
    )

    disks: tuple[VirtualDisk, ...] = ()
    adapters: tuple[NetworkAdapter, ...] = ()
    snapshots: SnapshotSummary = field(default_factory=SnapshotSummary)

    # 관계 — native_id 문자열 참조 (FK 아님, 계획 06 §5.1)
    host_native_id: str | None = None
    cluster_native_id: str | None = None
    resource_pool: str | None = None     # Hyper-V는 항상 None
    folder_path: str | None = None       # Hyper-V는 항상 None

    annotation: str | None = None
    # 하이퍼바이저의 사용자 정의 속성 (이름, 값) 쌍. vCenter Custom Attributes만 채워진다.
    # vSphere Tags는 REST 전용 API라 수집하지 않는다 (D-010). Hyper-V는 항상 빈 튜플.
    custom_attributes: tuple[tuple[str, str], ...] = ()
    created_at: datetime | None = None

    @property
    def total_provisioned_bytes(self) -> int:
        return sum(d.provisioned_bytes for d in self.disks)

    @property
    def total_used_bytes(self) -> int | None:
        values = [d.used_bytes for d in self.disks if d.used_bytes is not None]
        return sum(values) if values else None

    @property
    def all_ipv4(self) -> tuple[str, ...]:
        return self.guest.ipv4_addresses

    @property
    def mac_addresses(self) -> tuple[str, ...]:
        return tuple(a.mac_address for a in self.adapters if a.mac_address)
```

### 6.3 나머지 엔티티

```python
@dataclass
class Host(ResourceBase):
    fqdn: str | None = None
    management_ip: str | None = None
    connection_state: ConnectionState = ConnectionState.UNKNOWN
    in_maintenance: bool = False
    boot_time: datetime | None = None
    # 하드웨어
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None     # 서비스 태그
    cpu_model: str | None = None
    cpu_sockets: int | None = None
    cpu_cores: int | None = None
    cpu_mhz: int | None = None
    memory_bytes: int | None = None
    # 소프트웨어
    hypervisor_product: str | None = None
    hypervisor_version: str | None = None
    hypervisor_build: str | None = None
    cluster_native_id: str | None = None
    physical_nics: tuple[PhysicalNic, ...] = ()


@dataclass
class Cluster(ResourceBase):
    host_count: int = 0
    vm_count: int = 0
    total_cpu_cores: int | None = None
    total_memory_bytes: int | None = None
    ha_enabled: bool | None = None       # vCenter HA / Hyper-V Failover
    drs_enabled: bool | None = None      # vCenter 전용


@dataclass
class Datastore(ResourceBase):
    kind: DatastoreKind = DatastoreKind.UNKNOWN
    capacity_bytes: int | None = None
    free_bytes: int | None = None
    provisioned_bytes: int | None = None  # 오버커밋 판단용
    url: str | None = None
    accessible: bool = True
    host_native_ids: tuple[str, ...] = ()

    @property
    def overcommit_ratio(self) -> float | None:
        if not self.capacity_bytes or self.provisioned_bytes is None:
            return None
        return self.provisioned_bytes / self.capacity_bytes


@dataclass
class Network(ResourceBase):
    kind: NetworkKind = NetworkKind.UNKNOWN
    vlan_id: int | None = None
    host_native_ids: tuple[str, ...] = ()
    connected_vm_count: int = 0


@dataclass
class Snapshot(ResourceBase):
    vm_native_id: str = ""
    description: str | None = None
    created_at_hv: datetime | None = None
    size_bytes: int | None = None
    parent_native_id: str | None = None
    is_current: bool = False
```

---

## 7. CI 식별 규칙 (`identity.py`) — FR-302

`docs/00_research_notes.md` §2.4의 식별 규칙 개념 구현. 우선순위가 높은 규칙부터 평가한다.

```python
class IdentityRule(IntEnum):
    NATIVE = 1       # connection_id + native_id  — 가장 신뢰
    BIOS_UUID = 2
    MAC_AND_NAME = 3


@dataclass(frozen=True, slots=True)
class IdentityKey:
    rule: IdentityRule
    value: str


def build_vm_identity_keys(vm: VirtualMachine) -> list[IdentityKey]:
    """우선순위 순으로 식별 키를 생성한다.

    MAC은 정렬하여 순서 변화가 키에 영향을 주지 않게 한다.
    """
    keys = [IdentityKey(IdentityRule.NATIVE, f"{vm.connection_id}:{vm.native_id}")]

    if vm.bios_uuid:
        keys.append(IdentityKey(IdentityRule.BIOS_UUID, vm.bios_uuid.lower()))

    macs = sorted(m.lower() for m in vm.mac_addresses)
    if macs:
        keys.append(IdentityKey(IdentityRule.MAC_AND_NAME, f"{'|'.join(macs)}::{vm.name}"))

    return keys


def build_generic_identity_keys(res: ResourceBase) -> list[IdentityKey]:
    """VM 외 자원은 1순위만 사용한다. 대체 식별자가 마땅치 않다."""
    return [IdentityKey(IdentityRule.NATIVE, f"{res.connection_id}:{res.native_id}")]
```

### 7.1 규칙별 유효성과 한계

| 순위 | 키 | 유효 조건 | 한계 | 대응 |
|---|---|---|---|---|
| 1 | `connection_id:native_id` | 항상 | 연결 재등록 시 깨짐 | 연결 ID 불변성(FR-110)을 UI에서 강제 |
| 2 | `bios_uuid` | 클론이 아닌 VM | 클론 시 중복, 수집 안 될 수 있음 | 단독 신뢰하지 않음 |
| 3 | `MAC들 + 이름` | 1·2 실패 시 | MAC 재사용·이름 중복 시 오탐 | 다른 연결이면 병합하지 않음 |

### 7.2 교차 연결 매칭 처리 (FR-308)

2·3순위 매칭은 **다른 연결의 자원과 매칭될 수 있다**. ELM 환경이나 중복 등록 시 발생한다.

```python
@dataclass(frozen=True)
class DuplicateCandidate:
    existing_resource_id: UUID
    existing_connection_id: UUID
    incoming_connection_id: UUID
    matched_rule: IdentityRule
    matched_value: str
    detected_at: datetime
```

**자동 병합하지 않는다** (D-006). 어느 연결이 권위 있는지 알 수 없고, 잘못된 병합은 되돌리기 어렵다.
경고만 남기고 1순위 기준으로 별도 유지한다.

---

## 8. 속성 출처 우선순위 (FR-304)

`docs/00_research_notes.md` §2.5의 조정 규칙. **권위 있는 소스**를 속성별로 지정한다.

| 속성 | 권위 있는 소스 | 대체 | 규칙 |
|---|---|---|---|
| 게스트 OS 이름 | 도구 감지값 | VM 구성값 | 도구 값 우선, 없으면 구성값 + `os_source=VM_CONFIG` |
| 게스트 호스트명·IP | 도구 감지값 | 없음 | 도구 없으면 수집 불가 |
| MAC·vCPU·메모리·디스크 | 하이퍼바이저 구성 | — | 도구 불필요 |
| **소유자·환경·용도·태그** | **포탈 입력** | — | **수집이 절대 덮어쓰지 않음** |
| 사용자 정의 속성(`custom_attributes`) | 하이퍼바이저 수집 | — | 수집값 전용 필드. 포탈 메타데이터와 **별개 필드**이며 초기값 참고용 (FR-606, D-010) |

```python
def resolve_os_name(tools_value: str | None, config_value: str | None) -> tuple[str | None, OsSource | None]:
    """게스트 OS 이름과 그 출처를 결정한다."""
    if tools_value:
        return tools_value, OsSource.GUEST_TOOLS
    if config_value:
        return config_value, OsSource.VM_CONFIG
    return None, None
```

---

## 9. 정규화 매핑 (FR-301)

어댑터가 참조할 대응 규칙. 속성 단위 매핑은 `spec.md` §2.2.

### 9.1 전원 상태

```python
VCENTER_POWER_MAP: dict[str, PowerState] = {
    "poweredOn": PowerState.ON,
    "poweredOff": PowerState.OFF,
    "suspended": PowerState.SUSPENDED,
}

# Hyper-V Msvm_ComputerSystem.EnabledState (CIM 표준 정수) — 경로 A (계획 05 §8.1)
# 표시 문자열은 로케일 영향을 받으므로 정수값을 쓴다.
# SCVMM(경로 B)은 VirtualMachineState 열거형 이름을 주며, 이는 로케일 무관이다.
HYPERV_ENABLED_STATE_MAP: dict[int, PowerState] = {
    2: PowerState.ON,          # Enabled
    3: PowerState.OFF,         # Disabled
    6: PowerState.SUSPENDED,   # Offline (Saved)
    9: PowerState.SUSPENDED,   # Paused
    32768: PowerState.SUSPENDED,  # Paused (벤더 확장)
    32769: PowerState.SUSPENDED,  # Suspended (벤더 확장)
}
```

> `[TODO]` Hyper-V의 `Saved`와 `Paused`를 모두 `SUSPENDED`로 볼지 확정 필요.
> 구분이 필요하면 `PowerState`에 값을 추가하고 `docs/02_decision.md`에 기록한다.

### 9.2 개념 대응

| 공통 | vCenter | Hyper-V | 규칙 |
|---|---|---|---|
| Cluster | ClusterComputeResource | Failover Cluster | 단독 호스트면 `cluster_native_id=None`. **가상 클러스터를 만들지 않는다** |
| Datastore | Datastore | CSV / SMB / 로컬 | 용량을 **바이트로 통일**. vCenter는 바이트, Hyper-V는 단위 확인 후 변환 |
| Network | 포트그룹 | 가상 스위치 | `NetworkKind`로 원본 유형 보존 |
| Snapshot | Snapshot | 체크포인트 | 공통 명칭 "스냅샷", UI에서 원본 병기 |
| ResourcePool | 있음 | 없음 | `None` + capability False |
| Datacenter/Folder | 있음 | 없음 | `None` |
| 디스크 프로비저닝 | thinProvisioned | 동적/고정 | `DiskProvisioning` |
| 펌웨어 | config.firmware | Generation 1/2 | Gen1→BIOS, Gen2→UEFI |

### 9.3 정규화 유틸 (`src/utils/net.py`)

어댑터 양쪽이 쓰므로 utils에 둔다. **어댑터끼리 참조하면 arch-check 위반**이다.

```python
def normalize_mac(raw: str | None) -> str | None:
    """MAC 주소를 소문자 콜론 구분 형식으로 정규화한다.

    00-15-5D-01-02-03 / 00155D010203 / 00:15:5d:01:02:03 → 00:15:5d:01:02:03
    """
    if not raw:
        return None
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2)).lower()


def normalize_ip(raw: str | None) -> str | None:
    """IP 주소를 정규화한다. 유효하지 않으면 None."""
    if not raw:
        return None
    try:
        return str(ip_address(raw.strip()))
    except ValueError:
        return None


def is_reportable_ip(addr: str) -> bool:
    """인벤토리에 표시할 가치가 있는 IP인지 판정한다.

    링크로컬(169.254.x, fe80::), 루프백은 제외한다.
    게스트 도구가 이런 주소까지 보고하므로 필터링이 필요하다.
    """
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def split_ip_families(addrs: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """IPv4/IPv6로 분리하고 정규화·필터링한다."""
    v4: list[str] = []
    v6: list[str] = []
    for raw in addrs:
        norm = normalize_ip(raw)
        if norm is None or not is_reportable_ip(norm):
            continue
        (v4 if ip_address(norm).version == 4 else v6).append(norm)
    return tuple(dict.fromkeys(v4)), tuple(dict.fromkeys(v6))   # 중복 제거, 순서 유지
```

---

## 10. 연결 정보 (`connection.py`)

```python
@dataclass
class Connection:
    connection_id: UUID                  # 불변 (FR-110)
    kind: ConnectionKind
    display_name: str
    address: str
    port: int
    username: str
    password: SecretStr                  # pydantic SecretStr — repr 마스킹
    protocol: Literal["http", "https"] = "https"
    auth_method: WinRmAuth | None = None # Hyper-V만 사용
    session_configuration: str | None = None   # JEA 엔드포인트 이름 (계획 05 §4.3.1). None이면 기본 세션
    verify_tls: bool = True
    description: str | None = None
    collection_interval_minutes: int = 360
    collectable_types: frozenset[ResourceType] | None = None   # None = 전체 (FR-208)
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    created_by: str | None = None
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None

    def __repr__(self) -> str:
        """자격증명이 로그에 노출되지 않도록 재정의한다 (NFR-203)."""
        return (
            f"Connection(id={self.connection_id}, kind={self.kind}, "
            f"name={self.display_name!r}, address={self.address}, status={self.status})"
        )

    @property
    def is_collectable(self) -> bool:
        """수집 대상 여부. 자격증명 오류 연결은 제외한다 (FR-114)."""
        return self.status is ConnectionStatus.ACTIVE

    def validate(self) -> None:
        """도메인 규칙 검증 (FR-104)."""
        if self.kind.hypervisor is HypervisorKind.HYPERV and self.auth_method is None:
            raise ValidationError("Hyper-V 연결은 인증 방식이 필요합니다.", field="auth_method")
        if not (1 <= self.port <= 65535):
            raise ValidationError("포트는 1~65535 범위여야 합니다.", field="port")
        if self.kind is ConnectionKind.VCENTER and self.protocol != "https":
            raise ValidationError("vCenter 연결은 HTTPS만 지원합니다.", field="protocol")
```

**`__repr__` 재정의가 중요하다.** dataclass 기본 `__repr__`은 모든 필드를 출력하며,
`SecretStr`이 자체적으로 마스킹하더라도 명시적으로 제외하는 편이 안전하다.

---

## 11. 메타데이터 (`metadata.py`)

**수집 엔티티와 완전히 분리한다.** 같은 테이블에 섞으면 재수집이 덮어쓸 위험이 상시 존재한다 (FR-602, D-006).

```python
class _Unset:
    """미지정을 나타내는 센티널. None(값 지움)과 구분한다."""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self) -> str:
        return "UNSET"

UNSET = _Unset()
Unset = _Unset


@dataclass
class ResourceMetadata:
    resource_id: UUID
    owner: str | None = None
    team: str | None = None
    purpose: str | None = None
    environment: Environment | None = None
    criticality: Criticality | None = None
    service_name: str | None = None
    cost_center: str | None = None
    lifecycle_note: str | None = None
    tags: frozenset[str] = frozenset()
    updated_by: str | None = None
    updated_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        """필수 메타데이터 충족 여부 (FR-503)."""
        return bool(self.owner) and self.environment is not None


@dataclass(frozen=True)
class MetadataPatch:
    """부분 갱신. UNSET은 변경 안 함, None은 값 지움."""
    owner: str | None | Unset = UNSET
    team: str | None | Unset = UNSET
    purpose: str | None | Unset = UNSET
    environment: Environment | None | Unset = UNSET
    criticality: Criticality | None | Unset = UNSET
    service_name: str | None | Unset = UNSET
    cost_center: str | None | Unset = UNSET
    lifecycle_note: str | None | Unset = UNSET
    tags: frozenset[str] | Unset = UNSET

    def changed_fields(self) -> dict[str, object]:
        """UNSET이 아닌 필드만 반환한다."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if not isinstance(getattr(self, f.name), _Unset)
        }
```

**UNSET 센티널을 빠뜨리면** 일부 필드만 수정할 때 나머지가 지워지는 버그가 생긴다.

---

## 12. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `enums.py` | 전원 상태 매핑 테이블이 vCenter/Hyper-V 값을 모두 커버 |
| 2 | `exceptions.py` | `AuthenticationError.retryable is False`, 계층 관계 |
| 3 | `src/utils/net.py` | MAC 3가지 표기 정규화, 링크로컬·루프백 필터, 중복 제거 |
| 4 | `src/utils/retry.py` | retryable=False 예외 즉시 전파, 지수 백오프 간격 |
| 5 | `values.py` — `GuestInfo` | `is_collected`, `with_fallback`이 이전 값 유지 |
| 6 | `values.py` — 나머지 | 불변성(`frozen`), 계산 속성 |
| 7 | `resource.py` | `spec.md` §2.2 필수(✔) 속성 전수 대조 |
| 8 | `identity.py` | MAC 순서 무관 동일 키, 우선순위 정렬 |
| 9 | `connection.py` | `__repr__`에 비밀번호 미노출, `validate()` |
| 10 | `metadata.py` | UNSET vs None 구분, `changed_fields()` |
| 11 | `history.py`·`auth.py`·`audit.py` 골격 | 계획 12·09·10에서 상세화 |

## 13. 완료 기준

- [ ] `spec.md` §2.2의 필수(✔) 속성이 모두 엔티티에 존재
- [ ] `arch_check.py` 통과 — 도메인이 내부 모듈을 import하지 않음
- [ ] `GuestInfo`로 "값 없음"과 "수집 불가"가 구분됨
- [ ] `with_fallback`이 도구 미동작 시 이전 값을 유지
- [ ] `build_vm_identity_keys`가 MAC 순서와 무관하게 동일 키 생성
- [ ] `AuthenticationError.retryable is False`, `retry_async`가 즉시 전파
- [ ] `normalize_mac`이 `00-15-5D-01-02-03`·`00155D010203`·`00:15:5D:01:02:03`을 같은 값으로 반환
- [ ] `is_reportable_ip`가 `169.254.x.x`·`fe80::`·`127.0.0.1`을 제외
- [ ] `Connection`을 `repr()`·`f-string`·로그에 넣어도 비밀번호 미노출
- [ ] `MetadataPatch`로 한 필드만 수정 시 나머지 필드 유지
- [ ] mypy strict 통과

## 14. 주의사항

- **엔티티가 SQLAlchemy 모델을 겸하게 하지 않는다.** 매핑 코드가 늘더라도 계층을 지킨다 (계획 06).
- 메타데이터를 자원 엔티티에 넣고 싶은 유혹이 있으나, **분리가 FR-602의 구조적 보장**이다.
- 클론 VM은 BIOS UUID가 중복될 수 있으므로 2순위 규칙을 단독 신뢰하지 않는다.
- `slots=True`는 메모리 절감에 유효하나 상속 시 제약이 있다. `ResourceBase` 상속 계층에는 쓰지 않는다.
- `datetime`은 전부 timezone-aware(UTC)로 다룬다. naive datetime이 섞이면 비교 시 예외가 난다.
