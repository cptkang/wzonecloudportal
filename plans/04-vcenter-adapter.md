# 04. vCenter 수집 어댑터

> Wave: 2 · 계층: infrastructure (`src/infrastructure/vcenter/`)
> 담당 요건: FR-203, FR-106, FR-301, FR-501, `spec.md` §2.2 vCenter 출처 열
> 의존: 02, 03 · 관련 결정: D-003, D-005, D-007

## 1. 목적

pyVmomi로 vCenter 인벤토리를 수집하여 공통 도메인 모델로 반환한다.

**`src/infrastructure/hyperv`를 절대 import하지 않는다** (arch-check 특화 규칙 7).
공통 로직이 필요하면 `src/utils/` 또는 `src/domain/`에 두고 양쪽이 각각 참조한다.

## 2. 모듈 구성

```
src/infrastructure/vcenter/
├── __init__.py          VCenterInventoryReader export
├── reader.py            Protocol 구현 (진입점)
├── session.py           SmartConnect 세션 관리
├── collector.py         PropertyCollector 페이징 조회
├── property_specs.py    자원 유형별 수집 속성 목록
├── mapper.py            pyVmomi 속성 dict → 도메인 모델
└── errors.py            pyVmomi 예외 → 도메인 예외
```

---

## 3. 세션 관리 (`session.py`)

```python
import ssl
import asyncio
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim


class VCenterSession:
    """vCenter 연결 세션.

    pyVmomi는 동기 라이브러리이므로 모든 호출을 asyncio.to_thread로 오프로드한다.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._si: vim.ServiceInstance | None = None

    @property
    def content(self) -> vim.ServiceInstanceContent:
        if self._si is None:
            raise CollectionError("세션이 열려 있지 않습니다.")
        return self._si.RetrieveContent()

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """TLS 검증 정책에 따른 컨텍스트 (FR-115)."""
        if self._conn.verify_tls:
            return None                              # 기본 검증 사용
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect_sync(self) -> vim.ServiceInstance:
        return SmartConnect(
            host=self._conn.address,
            port=self._conn.port,
            user=self._conn.username,
            pwd=self._conn.password.get_secret_value(),   # 이 지점에서만 복호화
            sslContext=self._build_ssl_context(),
            connectionPoolTimeout=-1,                     # 풀링 비활성 — 세션 수명 직접 관리
        )

    async def start_session(self) -> None:
        try:
            self._si = await asyncio.to_thread(self._connect_sync)
        except Exception as exc:
            raise translate_error(exc) from None          # 원본 예외 체이닝 차단 (자격증명 노출 방지)

    async def close_session(self) -> None:
        if self._si is None:
            return
        try:
            await asyncio.to_thread(Disconnect, self._si)
        except Exception:
            logger.warning("vCenter 세션 종료 실패", extra={"connection_id": str(self._conn.connection_id)})
        finally:
            self._si = None

    async def __aenter__(self) -> "VCenterSession":
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()
```

### 3.1 주의점

- **`raise ... from None`**: pyVmomi 예외 메시지에 접속 정보가 섞일 수 있다. 체이닝을 끊고 정제된 메시지만 남긴다 (계획 10 §2.4).
- **세션 누수**: vCenter는 유휴 세션을 일정 시간 유지한다. 수집 실패 시에도 반드시 `close_session`을 호출한다. `__aexit__`로 보장한다.
- **`connectionPoolTimeout`**: pyVmomi 버전에 따라 파라미터명이 다를 수 있다. `[검증 필요]`
- vCenter는 HTTPS만 지원한다 (`Connection.validate()`에서 강제, 계획 02 §10).

---

## 4. PropertyCollector 조회 (`collector.py`) — 이 계획의 핵심

`docs/00_research_notes.md` §4.2. **자원을 하나씩 순회하며 개별 속성을 읽으면 안 된다.**

### 4.1 FilterSpec 구성

```python
from pyVmomi import vim, vmodl

PC = vmodl.query.PropertyCollector


def build_filter_spec(
    container_view: vim.view.ContainerView,
    obj_type: type,
    path_set: list[str],
) -> PC.FilterSpec:
    """ContainerView를 순회하며 지정 속성만 조회하는 FilterSpec을 만든다."""
    traversal = PC.TraversalSpec(
        name="traverseEntities",
        path="view",                         # ContainerView.view 속성을 따라 순회
        skip=False,
        type=vim.view.ContainerView,
    )
    obj_spec = PC.ObjectSpec(
        obj=container_view,
        skip=True,                           # 컨테이너 자체는 결과에서 제외
        selectSet=[traversal],
    )
    prop_spec = PC.PropertySpec(
        type=obj_type,
        all=False,                           # 전체 속성 조회 금지 — 응답이 거대해진다
        pathSet=path_set,
    )
    return PC.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])
```

### 4.2 페이징 조회

```python
DEFAULT_PAGE_SIZE = 500


class PropertyCollectorReader:
    def __init__(self, session: VCenterSession, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._session = session
        self._page_size = page_size

    def _create_view_sync(self, obj_type: type) -> vim.view.ContainerView:
        content = self._session.content
        return content.viewManager.CreateContainerView(
            container=content.rootFolder, type=[obj_type], recursive=True
        )

    def _release_view_sync(self, view: vim.view.ContainerView) -> None:
        """ContainerView 자원을 해제한다.

        DestroyView는 뷰 객체 정리이며 하이퍼바이저 자원 삭제가 아니다.
        메서드명 오해를 피하기 위해 래퍼 이름을 release_view로 둔다.
        """
        try:
            view.DestroyView()
        except Exception:
            logger.debug("ContainerView 해제 실패 (무시)")

    def _retrieve_page_sync(
        self, filter_spec: PC.FilterSpec, token: str | None
    ) -> PC.RetrieveResult | None:
        pc = self._session.content.propertyCollector
        if token is None:
            options = PC.RetrieveOptions(maxObjects=self._page_size)
            return pc.RetrievePropertiesEx(specSet=[filter_spec], options=options)
        return pc.ContinueRetrievePropertiesEx(token=token)

    async def retrieve(
        self, obj_type: type, path_set: list[str]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """(MoRef ID, 속성 dict) 튜플을 페이지 단위로 yield한다."""
        view = await asyncio.to_thread(self._create_view_sync, obj_type)
        try:
            filter_spec = build_filter_spec(view, obj_type, path_set)
            token: str | None = None
            while True:
                result = await asyncio.to_thread(self._retrieve_page_sync, filter_spec, token)
                if result is None:
                    break
                for obj_content in result.objects:
                    yield _moref_id(obj_content.obj), _props_to_dict(obj_content)
                token = getattr(result, "token", None)
                if not token:
                    break
        finally:
            await asyncio.to_thread(self._release_view_sync, view)


def _moref_id(managed_object: Any) -> str:
    """Managed Object Reference를 문자열 ID로 변환한다. 예: 'vm-1234'"""
    return str(managed_object._moId)


def _props_to_dict(obj_content: PC.ObjectContent) -> dict[str, Any]:
    """propSet을 dict로 변환하고 missSet(조회 실패 속성)을 None으로 채운다."""
    props: dict[str, Any] = {p.name: p.val for p in (obj_content.propSet or [])}
    for miss in (obj_content.missingSet or []):
        props[miss.path] = None            # 권한 부족·미지원 속성
    return props
```

### 4.3 주의점

- **`token` 반복이 필수다.** 첫 응답만 처리하면 `maxObjects` 초과분이 누락된다. 이 누락은 조용히 발생하므로 테스트로 잡아야 한다.
- **`missingSet` 처리**: 권한 부족이나 버전 미지원 속성은 `propSet`이 아니라 `missingSet`에 온다. 무시하면 `KeyError`가 난다.
- **`maxObjects`가 너무 크면** 응답 크기가 커져 타임아웃이 난다. 500에서 시작해 환경에 맞춰 조정한다.
- **`DestroyView` 누락 시** vCenter에 뷰 객체가 누적된다. `finally`로 보장한다.

---

## 5. 수집 속성 목록 (`property_specs.py`)

**필요한 속성만 명시한다.** `all=True`나 넓은 `pathSet`은 응답을 폭증시킨다.

```python
VM_PROPERTIES: list[str] = [
    "name",
    "config.uuid",                      # BIOS UUID
    "config.instanceUuid",              # native_id (vCenter 인스턴스 내 고유)
    "config.version",                   # vmx-19
    "config.firmware",                  # bios | efi
    "config.guestFullName",             # 구성값 OS
    "config.annotation",
    "config.createDate",
    "config.hardware.numCPU",
    "config.hardware.numCoresPerSocket",
    "config.hardware.memoryMB",
    "config.hardware.device",           # 디스크·NIC 등 전체 장치
    "runtime.powerState",
    "runtime.connectionState",
    "runtime.host",                     # ManagedObjectReference
    "runtime.bootTime",
    "guest.guestFullName",              # 도구 감지값 OS
    "guest.hostName",
    "guest.net",                        # IP 목록
    "guest.toolsStatus",
    "guest.toolsRunningStatus",
    "guest.toolsVersion",
    "snapshot",                         # 스냅샷 트리
    "resourcePool",
    "parent",                           # 폴더
]

HOST_PROPERTIES: list[str] = [
    "name",
    "runtime.connectionState",
    "runtime.inMaintenanceMode",
    "runtime.bootTime",
    "hardware.systemInfo.vendor",
    "hardware.systemInfo.model",
    "hardware.systemInfo.serialNumber",  # 서비스 태그
    "hardware.cpuInfo.numCpuPackages",
    "hardware.cpuInfo.numCpuCores",
    "hardware.cpuInfo.hz",
    "hardware.cpuPkg",                   # CPU 모델명
    "hardware.memorySize",
    "config.product.name",
    "config.product.version",
    "config.product.build",
    "config.network.pnic",
    "config.network.vnic",               # 관리 IP
    "parent",                            # 클러스터
]

CLUSTER_PROPERTIES: list[str] = [
    "name", "host", "summary.numHosts", "summary.numEffectiveHosts",
    "summary.totalCpu", "summary.totalMemory", "summary.numCpuCores",
    "configuration.dasConfig.enabled",    # HA
    "configuration.drsConfig.enabled",    # DRS
]

DATASTORE_PROPERTIES: list[str] = [
    "name", "summary.type", "summary.capacity", "summary.freeSpace",
    "summary.uncommitted",                # 프로비저닝 초과분 (오버커밋 판단)
    "summary.url", "summary.accessible", "host",
]

NETWORK_PROPERTIES: list[str] = ["name", "summary.accessible", "host", "vm"]
DVPG_PROPERTIES: list[str] = ["name", "config.defaultPortConfig", "config.distributedVirtualSwitch", "host"]
```

> **[검증 필요]** (`docs/00_research_notes.md` §11-2): 속성 경로는 vSphere 버전에 따라 존재 여부가 다르다.
> 대상 vCenter에서 실제 조회하여 확인하고, 누락 속성은 예외가 아닌 `None`으로 처리한다 (`missingSet` 처리로 자동 대응).

### 5.1 프로비저닝 용량 계산

`summary.uncommitted`는 Thin 디스크의 미할당분이다. 프로비저닝 총량은:

```
provisioned = (capacity - freeSpace) + uncommitted
```

---

## 6. 매핑 (`mapper.py`)

### 6.1 VM 매핑

```python
def map_virtual_machine(
    connection_id: UUID, moid: str, props: dict[str, Any], observed_at: datetime
) -> VirtualMachine:
    devices = props.get("config.hardware.device") or []
    disks = tuple(_map_disk(d) for d in devices if isinstance(d, vim.vm.device.VirtualDisk))
    adapters = tuple(
        _map_adapter(d) for d in devices if isinstance(d, vim.vm.device.VirtualEthernetCard)
    )

    return VirtualMachine(
        resource_id=uuid4(),                       # 저장소가 기존 자원 매칭 시 교체
        connection_id=connection_id,
        native_id=props.get("config.instanceUuid") or moid,
        name=props.get("name") or moid,
        bios_uuid=props.get("config.uuid"),
        power_state=VCENTER_POWER_MAP.get(str(props.get("runtime.powerState")), PowerState.UNKNOWN),
        connection_state=_map_connection_state(props.get("runtime.connectionState")),
        boot_time=props.get("runtime.bootTime"),
        cpu=CpuSpec(
            total_vcpu=props.get("config.hardware.numCPU") or 0,
            cores_per_socket=props.get("config.hardware.numCoresPerSocket"),
            socket_count=_socket_count(props),
        ),
        memory=MemorySpec(assigned_mb=props.get("config.hardware.memoryMB") or 0),
        platform=PlatformSpec(
            hardware_version=props.get("config.version"),
            firmware=Firmware.UEFI if props.get("config.firmware") == "efi" else Firmware.BIOS,
            configured_os=props.get("config.guestFullName"),
        ),
        guest=map_guest_info(props, observed_at),
        disks=disks,
        adapters=adapters,
        snapshots=_map_snapshot_summary(props.get("snapshot")),
        host_native_id=_moref_or_none(props.get("runtime.host")),
        cluster_native_id=None,                    # 호스트→클러스터 해석은 저장소/조회 시점
        resource_pool=_moref_or_none(props.get("resourcePool")),
        folder_path=None,                          # parent 체인 해석은 §6.4
        annotation=props.get("config.annotation"),
        created_at=props.get("config.createDate"),
        last_seen_at=observed_at,
    )


def _socket_count(props: dict[str, Any]) -> int | None:
    total = props.get("config.hardware.numCPU")
    per_socket = props.get("config.hardware.numCoresPerSocket")
    if not total or not per_socket:
        return None
    return total // per_socket
```

### 6.2 게스트 정보 매핑 (FR-501) — 주의 지점

`docs/00_research_notes.md` §6: Tools가 없으면 IP·OS·호스트명을 얻을 수 없다.
§6.3: Tools 상태 보고가 부정확한 알려진 버그가 있으므로 **상태만 믿지 말고 값 유무도 함께 본다.**

```python
def map_guest_info(props: dict[str, Any], observed_at: datetime) -> GuestInfo:
    tools_status = props.get("guest.toolsStatus")
    running = props.get("guest.toolsRunningStatus")

    if tools_status == "toolsNotInstalled":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    if running is not None and running != "guestToolsRunning":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    hostname = props.get("guest.hostName")
    nets = props.get("guest.net") or []
    raw_ips = [ip for n in nets for ip in (getattr(n, "ipAddress", None) or [])]
    v4, v6 = split_ip_families(raw_ips)             # 링크로컬·루프백 제거 (계획 02 §9.3)

    if not hostname and not v4 and not v6:
        # 상태는 정상인데 값이 없음 — 부팅 직후일 수 있다
        return GuestInfo(availability=GuestInfoAvailability.UNKNOWN)

    os_name, os_source = resolve_os_name(
        props.get("guest.guestFullName"), props.get("config.guestFullName")
    )
    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_source=os_source,
        hostname=hostname,
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        tool_version=props.get("guest.toolsVersion"),
        observed_at=observed_at,
    )
```

**`guest.net[].ipAddress`에는 링크로컬(`fe80::`)이 포함된다.** 필터링하지 않으면 목록·검색이 오염된다.

### 6.3 장치 매핑

```python
def _map_disk(dev: vim.vm.device.VirtualDisk) -> VirtualDisk:
    backing = dev.backing
    thin = getattr(backing, "thinProvisioned", None)
    return VirtualDisk(
        key=str(dev.key),
        label=getattr(dev.deviceInfo, "label", None),
        provisioned_bytes=(dev.capacityInKB or 0) * 1024,
        used_bytes=None,                    # Thin 실제 사용량 — §6.5 참조
        provisioning=(
            DiskProvisioning.THIN if thin is True
            else DiskProvisioning.THICK if thin is False
            else DiskProvisioning.UNKNOWN
        ),
        datastore_name=_datastore_name_from_path(getattr(backing, "fileName", None)),
        file_path=getattr(backing, "fileName", None),
    )


ADAPTER_TYPE_MAP = {
    vim.vm.device.VirtualVmxnet3: "vmxnet3",
    vim.vm.device.VirtualVmxnet2: "vmxnet2",
    vim.vm.device.VirtualE1000e: "e1000e",
    vim.vm.device.VirtualE1000: "e1000",
    vim.vm.device.VirtualPCNet32: "pcnet32",
}


def _map_adapter(dev: vim.vm.device.VirtualEthernetCard) -> NetworkAdapter:
    return NetworkAdapter(
        key=str(dev.key),
        mac_address=normalize_mac(dev.macAddress),           # 계획 02 §9.3
        adapter_type=next(
            (v for k, v in ADAPTER_TYPE_MAP.items() if isinstance(dev, k)),
            type(dev).__name__,
        ),
        network_name=_network_name(dev.backing),
        connected=getattr(dev.connectable, "connected", None),
    )


def _network_name(backing: Any) -> str | None:
    """표준 포트그룹과 분산 포트그룹의 이름 추출 경로가 다르다."""
    if hasattr(backing, "deviceName"):                       # 표준 포트그룹
        return backing.deviceName
    port = getattr(backing, "port", None)                    # 분산 포트그룹
    return getattr(port, "portgroupKey", None)               # 키 → 이름 해석은 §6.4
```

> 분산 포트그룹은 `portgroupKey`(MoRef)만 얻어진다. 이름으로 바꾸려면 별도로 수집한 Network 목록과 대조해야 한다.
> **매핑 단계에서 키를 저장하고, 이름 해석은 수집 완료 후 후처리**로 수행한다 (§6.4).

### 6.4 참조 해석 후처리

VM 매핑 시점에는 호스트·클러스터·폴더·포트그룹 이름을 모른다.
**수집 순서에 의존하지 않도록 MoRef를 저장하고, 조회 시점에 해석한다** (계획 06 §5.1).

단, 클러스터는 `호스트 → 클러스터` 관계로만 알 수 있으므로 수집 후처리에서 채운다.

```python
def resolve_vm_cluster(vms: list[VirtualMachine], hosts: dict[str, Host]) -> None:
    """호스트 MoRef로 클러스터를 역참조한다. 수집 완료 후 실행."""
    for i, vm in enumerate(vms):
        host = hosts.get(vm.host_native_id or "")
        if host and host.cluster_native_id:
            vms[i] = replace(vm, cluster_native_id=host.cluster_native_id)
```

### 6.5 Thin 디스크 실제 사용량

`capacityInKB`는 프로비저닝 용량이다. 실제 사용량은 별도 경로가 필요하다.

**1차 구현에서는 `used_bytes=None`으로 둔다.** 확보 방법(`vim.vm.Summary.StorageSummary.committed` 등)은
`[TODO]`로 남기고, 필요해지면 별도로 조사한다. 용량 리포트(FR-805)는 데이터스토어 레벨 값으로 대체 가능하다.

---

## 7. 연결 테스트 (`reader.py`)

```python
async def check_connection(self) -> ConnectionCheckResult:
    runner = StageRunner()                                   # 계획 03 §4.1

    await runner.run(CheckStage.REACHABLE, self._check_reachable)
    await runner.run(CheckStage.TLS_VALID, self._check_tls)
    await runner.run(CheckStage.AUTHENTICATED, self._check_auth)

    readable: set[ResourceType] = set()
    await runner.run(CheckStage.AUTHORIZED, lambda: self._check_authorized(readable))

    return ConnectionCheckResult(
        stages=runner.results,
        readable_types=frozenset(readable),
        server_version=self._server_version,
    )


async def _check_reachable(self) -> str | None:
    """TCP 연결만 확인한다. 짧은 타임아웃을 쓴다."""
    fut = asyncio.open_connection(self._conn.address, self._conn.port)
    reader, writer = await asyncio.wait_for(fut, timeout=5)
    writer.close()
    await writer.wait_closed()
    return f"{self._conn.address}:{self._conn.port}"


async def _check_authorized(self, readable: set[ResourceType]) -> str | None:
    """자원 유형별로 1건만 조회하여 권한을 확인한다. 전량 조회는 부적절하다."""
    for rtype, obj_type, props in (
        (ResourceType.VIRTUAL_MACHINE, vim.VirtualMachine, ["name"]),
        (ResourceType.HOST, vim.HostSystem, ["name"]),
        (ResourceType.DATASTORE, vim.Datastore, ["name"]),
        (ResourceType.NETWORK, vim.Network, ["name"]),
    ):
        try:
            probe = PropertyCollectorReader(self._session, page_size=1)
            async for _ in probe.retrieve(obj_type, props):
                break
            readable.add(rtype)
        except vim.fault.NoPermission:
            continue                                          # 이 유형만 실패, 나머지 계속
    if not readable:
        raise PermissionError("조회 권한이 있는 자원 유형이 없습니다.")
    return f"조회 가능: {', '.join(sorted(t.value for t in readable))}"
```

---

## 8. 예외 변환 (`errors.py`)

**pyVmomi 예외가 어댑터 밖으로 나가면 안 된다.**

```python
def translate_error(exc: Exception) -> PortalError:
    """pyVmomi 예외를 도메인 예외로 변환한다. 메시지에서 자격증명을 제거한다."""
    if isinstance(exc, vim.fault.InvalidLogin):
        return AuthenticationError("인증에 실패했습니다. 계정 또는 비밀번호를 확인하세요.")
    if isinstance(exc, vim.fault.NoPermission):
        priv = getattr(exc, "privilegeId", None)
        return PermissionError(
            "조회 권한이 부족합니다." + (f" (필요 권한: {priv})" if priv else "")
        )
    if isinstance(exc, vim.fault.HostConnectFault):
        return UnreachableError("vCenter에 연결할 수 없습니다.")
    if isinstance(exc, (ssl.SSLError, ssl.SSLCertVerificationError)):
        return UnreachableError(
            "TLS 인증서 검증에 실패했습니다. 자체 서명 인증서라면 검증을 비활성화하세요."
        )
    if isinstance(exc, (socket.timeout, asyncio.TimeoutError, TimeoutError)):
        return UnreachableError("응답 시간이 초과되었습니다.")
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, OSError)):
        return UnreachableError("네트워크 연결에 실패했습니다.")
    if isinstance(exc, vmodl.MethodFault):
        return CollectionError(f"vCenter 오류: {sanitize_message(getattr(exc, 'msg', ''))}")
    return CollectionError(f"알 수 없는 오류: {sanitize_message(str(exc))}")
```

| pyVmomi 예외 | 도메인 예외 | retryable |
|---|---|---|
| `vim.fault.InvalidLogin` | `AuthenticationError` | **False** |
| `vim.fault.NoPermission` | `PermissionError` | False |
| `vim.fault.HostConnectFault` | `UnreachableError` | True |
| SSL 오류 | `UnreachableError` | True |
| 타임아웃·소켓 오류 | `UnreachableError` | True |
| 기타 `vmodl.MethodFault` | `CollectionError` | False |

**`InvalidLogin` → `AuthenticationError` 매핑이 계정 잠금 방지의 출발점이다** (FR-114, CST-05).

`sanitize_message`는 계획 10 §2.5의 마스킹 패턴을 재사용한다.

---

## 9. Reader 구현 (`reader.py`)

```python
class VCenterInventoryReader:
    """HypervisorInventoryReader 구현 (vCenter)."""

    def __init__(self, connection: Connection, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._conn = connection
        self._session = VCenterSession(connection)
        self._pc = PropertyCollectorReader(self._session, page_size)
        self._outcomes: list[CollectionOutcome] = []
        self._host_cache: dict[str, Host] = {}

    @property
    def capabilities(self) -> ReaderCapabilities:
        return VCENTER_CAPABILITIES

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        observed_at = datetime.now(UTC)
        started = time.monotonic()
        count = 0
        try:
            async for moid, props in self._pc.retrieve(vim.VirtualMachine, VM_PROPERTIES):
                count += 1
                yield map_virtual_machine(self._conn.connection_id, moid, props, observed_at)
        except AuthenticationError:
            raise                                    # 세션 무효 — 전파
        except PortalError as exc:
            self._record(ResourceType.VIRTUAL_MACHINE, count, failed=True, error=str(exc))
            return
        self._record(ResourceType.VIRTUAL_MACHINE, count, failed=False, started=started)
```

나머지 `list_*`도 동일 패턴이다. 중복을 줄이려면 제네릭 헬퍼로 감싼다.

```python
async def _collect(
    self, rtype: ResourceType, obj_type: type, props: list[str],
    mapper: Callable[[str, dict[str, Any], datetime], T],
) -> AsyncIterator[T]:
    """수집 공통 로직: 예외를 outcome으로 전환하고 통계를 기록한다."""
```

---

## 10. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `errors.py` | `InvalidLogin` → `AuthenticationError(retryable=False)`, 메시지에 자격증명 없음 |
| 2 | `session.py` | 연결/해제, 실패 경로에서도 세션 정리, TLS 검증 on/off |
| 3 | `property_specs.py` | **대상 vCenter에서 모든 속성 경로 실측** (§5 검증 필요) |
| 4 | `collector.py` 페이징 | `maxObjects`(=2) 초과 자원에서 토큰 반복 동작, `missingSet` 처리 |
| 5 | `mapper.py` — `map_guest_info` | Tools 상태 4가지 분기, 링크로컬 필터 |
| 6 | `mapper.py` — 장치 | 디스크 Thin/Thick, MAC 정규화, 분산 포트그룹 키 |
| 7 | `mapper.py` — VM 전체 | `spec.md` §2.2 필수 속성 매핑 |
| 8 | `mapper.py` — Host/Cluster/Datastore/Network | 용량 바이트 통일, 오버커밋 계산 |
| 9 | `reader.py` | **계약 테스트 스위트(계획 03 §9) 통과** |
| 10 | 연결 테스트 | 4단계 결과, 권한 부족 시 유형별 판정 |

## 11. 완료 기준

- [ ] `arch_check.py` 통과 — hyperv 미참조, 읽기 전용 메서드만
- [ ] 계약 테스트 스위트 14종 통과 (05와 동일 스위트)
- [ ] `maxObjects` 초과 자원이 전량 수집됨 (토큰 반복 확인)
- [ ] `missingSet` 속성이 `KeyError` 없이 `None`으로 처리됨
- [ ] Tools 미설치 VM이 `TOOLS_NOT_INSTALLED`로 매핑
- [ ] 링크로컬·루프백 IP가 결과에 없음
- [ ] MAC이 `00:50:56:aa:bb:cc` 형식으로 정규화
- [ ] pyVmomi 예외가 어댑터 밖으로 나오지 않음
- [ ] 세션이 실패 경로에서도 해제됨 (`__aexit__` 확인)
- [ ] **코드에 쓰기 API 호출이 없음** — `Destroy_Task`, `PowerOffVM_Task`, `ReconfigVM_Task`, `CreateVM_Task`, `RelocateVM_Task` 등 (verifier가 직접 확인, D-005 한계)

## 12. 주의사항

- **쓰기 API 호출 금지.** arch-check는 메서드명만 보므로 `get_vm_status()` 안의 `PowerOffVM_Task()`를 잡지 못한다. `grep -rE "_Task\(" src/infrastructure/vcenter/`로 확인하고, 조회용 `_Task`(없음)를 제외한 모든 사용을 검토한다.
- `DestroyView`는 뷰 정리이지 자원 삭제가 아니다. 래퍼 이름을 `release_view`로 두어 오해를 막는다.
- pyVmomi는 동기 라이브러리다. `asyncio.to_thread` 없이 호출하면 이벤트 루프가 멈춘다.
- `raise ... from None`으로 예외 체이닝을 끊는다. 원본 메시지에 접속 정보가 섞일 수 있다.
- 개발·테스트는 목 커넥터로 한다 (CST-04). 운영 vCenter에 붙지 않는다.
