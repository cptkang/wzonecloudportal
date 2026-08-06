# 05. Hyper-V 수집 어댑터

> Wave: 2 · 계층: infrastructure (`src/infrastructure/hyperv/`)
> 담당 요건: FR-103, FR-106, FR-301, FR-501, `spec.md` §2.2 Hyper-V 출처 열, CST-03·06
> 의존: 02, 03 · 관련 결정: D-003, D-005, D-008

## 1. 목적

WinRM(PowerShell Remoting) / WMI로 Hyper-V 인벤토리를 수집하여 공통 도메인 모델로 반환한다.

**`src/infrastructure/vcenter`를 절대 import하지 않는다** (arch-check 특화 규칙 7).

## 2. vCenter와의 근본적 차이

`docs/00_research_notes.md` §7.2: Hyper-V에는 중앙 관리 지점이 없다.

| 항목 | vCenter | Hyper-V |
|---|---|---|
| 관리 지점 | vCenter 1대가 전체 인벤토리 보유 | 호스트마다 자기 VM만 앎 |
| 연결 단위 | vCenter 1개 = 연결 1개 | **호스트/클러스터 1개 = 연결 1개** |
| 조회 | PropertyCollector 일괄 | 호스트별 PowerShell/WMI 호출 |
| 인증 | 사용자명+비밀번호 | **NTLM/Kerberos/CredSSP 선택 필요** |
| 성능 | API 호출 빠름 | **원격 PowerShell은 훨씬 느림** |

마지막 항목이 설계를 지배한다. **호스트당 호출 횟수를 최소화**하고, 여러 정보를 한 스크립트로 모아 조회한다.

## 3. 모듈 구성

```
src/infrastructure/hyperv/
├── __init__.py          HyperVInventoryReader export
├── reader.py            Protocol 구현 (진입점)
├── session.py           WinRM 세션, 인증 방식 분기
├── runner.py            PowerShell 실행 + JSON 파싱
├── scripts.py           PowerShell 스크립트 상수
├── mapper.py            JSON → 도메인 모델
└── errors.py            WinRM/WMI 예외 → 도메인 예외
```

---

## 4. 세션과 인증 (`session.py`) — 핵심 위험 지점

`docs/00_research_notes.md` §7.3: 인증 방식이 환경에 따라 다르며 **잘못 선택하면 접속 자체가 실패한다.**

```python
from pypsrp.wsman import WSMan
from pypsrp.powershell import PowerShell, RunspacePool


AUTH_MAP: dict[WinRmAuth, str] = {
    WinRmAuth.NTLM: "ntlm",
    WinRmAuth.KERBEROS: "kerberos",
    WinRmAuth.CREDSSP: "credssp",
}


class HyperVSession:
    """WinRM 세션. RunspacePool을 재사용하여 연결 비용을 줄인다."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._wsman: WSMan | None = None
        self._pool: RunspacePool | None = None

    def _open_sync(self) -> RunspacePool:
        if self._conn.auth_method is None:
            raise ValidationError("Hyper-V 연결은 인증 방식이 필요합니다.", field="auth_method")

        self._wsman = WSMan(
            server=self._conn.address,
            port=self._conn.port,
            username=self._conn.username,
            password=self._conn.password.get_secret_value(),   # 이 지점에서만 복호화
            ssl=(self._conn.protocol == "https"),
            auth=AUTH_MAP[self._conn.auth_method],
            cert_validation=self._conn.verify_tls,
            connection_timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        pool = RunspacePool(self._wsman)
        pool.open()
        return pool

    async def start_session(self) -> None:
        try:
            self._pool = await asyncio.to_thread(self._open_sync)
        except Exception as exc:
            raise translate_error(exc) from None

    async def close_session(self) -> None:
        if self._pool is not None:
            try:
                await asyncio.to_thread(self._pool.close)
            except Exception:
                logger.warning("WinRM 세션 종료 실패",
                               extra={"connection_id": str(self._conn.connection_id)})
            finally:
                self._pool = None
        if self._wsman is not None:
            try:
                await asyncio.to_thread(self._wsman.close)
            except Exception:
                pass
            finally:
                self._wsman = None
```

### 4.1 인증 방식 선택 가이드 (UI 도움말로 사용 — 계획 11 §6.1)

| 방식 | 사용 조건 | 주의 |
|---|---|---|
| **NTLM** | 도메인 미가입 호스트, 로컬 계정 | 대상에 `TrustedHosts` 등록이 필요할 수 있음 |
| **Kerberos** | 도메인 가입 + 도메인 계정 | **포탈 서버에도 krb5 설정 또는 도메인 가입 필요** |
| **CredSSP** | 이중 홉(second-hop) 필요 시 | **자격증명이 대상 서버로 위임된다.** 서버 침해 시 계정 노출 |

CredSSP는 읽기 전용 계정(NFR-201)이라 피해가 제한되지만, UI에서 경고를 표시한다.

> **[검증 필요]** (`docs/00_research_notes.md` §11-5)
> - `pypsrp`의 CredSSP 지원 범위와 추가 의존성(`pypsrp[credssp]`)
> - Kerberos 사용 시 포탈 서버(Linux 가능성)에 필요한 설정 — `python-gssapi`, `krb5.conf`
> - 비동기 API 제공 여부 (없으면 전부 `asyncio.to_thread`)
> 구현 착수 전 확인하고 결과를 조사 노트에 반영한다.

포트·프로토콜: HTTP 5985 / HTTPS 5986. **기본값은 HTTPS**, HTTP 선택 시 UI 경고.

---

## 5. PowerShell 실행 (`runner.py`)

### 5.1 실행과 파싱

```python
class PowerShellRunner:
    def __init__(self, session: HyperVSession) -> None:
        self._session = session

    def _invoke_sync(self, script: str, params: dict[str, Any] | None = None) -> str:
        ps = PowerShell(self._session.pool)
        ps.add_script(script)
        for k, v in (params or {}).items():
            ps.add_parameter(k, v)
        output = ps.invoke()
        if ps.had_errors:
            raise CollectionError(
                "PowerShell 실행 오류: " + "; ".join(str(e) for e in ps.streams.error[:3])
            )
        return "".join(str(o) for o in output)

    async def invoke_json(self, script: str, params: dict[str, Any] | None = None) -> list[dict]:
        """스크립트를 실행하고 JSON 출력을 리스트로 정규화한다."""
        raw = await asyncio.to_thread(self._invoke_sync, script, params)
        return parse_ps_json(raw)


def parse_ps_json(raw: str) -> list[dict]:
    """ConvertTo-Json 출력을 파싱한다.

    PowerShell은 항목이 1개일 때 배열이 아닌 객체를 반환하므로 정규화가 필요하다.
    """
    text = raw.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectionError(f"PowerShell JSON 파싱 실패: {exc}") from None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []
```

### 5.2 출력 형식 규칙

- **`ConvertTo-Json -Depth N -Compress`를 반드시 쓴다.** 텍스트 테이블 파싱은 로케일·컬럼 폭에 따라 깨진다
- `-Depth` 부족 시 중첩 객체가 `System.Object[]` 문자열로 잘린다. **최소 4, 중첩이 깊으면 6**
- `-Compress`로 개행을 줄여 전송량을 낮춘다
- 날짜는 PowerShell이 `/Date(1699999999000)/` 형식으로 직렬화할 수 있다. **`Get-Date -Format o`로 ISO 8601 문자열화**하여 파싱 문제를 없앤다

---

## 6. 수집 스크립트 (`scripts.py`)

**호출 횟수를 줄이기 위해 자원 유형별로 한 번씩만 호출한다.**

### 6.1 VM 기본 정보 + 네트워크 + 디스크

```python
SCRIPT_LIST_VMS = r"""
$ErrorActionPreference = 'Stop'
Get-VM | ForEach-Object {
    $vm = $_
    $kvp = @{}
    try {
        # KVP(게스트 정보)는 WMI로만 접근 가능 — 게스트 OS·IP의 유일한 경로
        $vmWmi = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
                 -Filter "Name='$($vm.Id)'"
        $kvpComp = Get-CimAssociatedInstance -InputObject $vmWmi `
                   -ResultClassName Msvm_KvpExchangeComponent -ErrorAction SilentlyContinue
        if ($kvpComp -and $kvpComp.GuestIntrinsicExchangeItems) {
            foreach ($item in $kvpComp.GuestIntrinsicExchangeItems) {
                $x = [xml]$item
                $n = ($x.INSTANCE.PROPERTY | Where-Object {$_.NAME -eq 'Name'}).VALUE
                $v = ($x.INSTANCE.PROPERTY | Where-Object {$_.NAME -eq 'Data'}).VALUE
                if ($n) { $kvp[$n] = $v }
            }
        }
    } catch { }

    $bios = $null
    try {
        $vssd = Get-CimInstance -Namespace root\virtualization\v2 `
                -ClassName Msvm_VirtualSystemSettingData `
                -Filter "ConfigurationID='$($vm.Id)'" -ErrorAction SilentlyContinue
        if ($vssd) { $bios = $vssd.BIOSGUID }
    } catch { }

    [PSCustomObject]@{
        Id              = $vm.Id.ToString()
        Name            = $vm.Name
        State           = [int]$vm.State
        Status          = $vm.Status
        Generation      = $vm.Generation
        Version         = $vm.Version
        ProcessorCount  = $vm.ProcessorCount
        MemoryAssigned  = $vm.MemoryAssigned
        MemoryStartup   = $vm.MemoryStartup
        MemoryMinimum   = $vm.MemoryMinimum
        MemoryMaximum   = $vm.MemoryMaximum
        DynamicMemory   = $vm.DynamicMemoryEnabled
        Uptime          = $vm.Uptime.TotalSeconds
        CreationTime    = if ($vm.CreationTime) { $vm.CreationTime.ToString('o') } else { $null }
        Notes           = $vm.Notes
        ComputerName    = $vm.ComputerName
        BiosGuid        = $bios
        IntegrationOk   = [bool]$kvp.Count
        KvpOSName       = $kvp['OSName']
        KvpOSVersion    = $kvp['OSVersion']
        KvpFQDN         = $kvp['FullyQualifiedDomainName']
        KvpIPv4         = $kvp['NetworkAddressIPv4']
        KvpIPv6         = $kvp['NetworkAddressIPv6']
        Adapters        = @($vm | Get-VMNetworkAdapter | ForEach-Object {
            [PSCustomObject]@{
                Id          = $_.Id
                Name        = $_.Name
                MacAddress  = $_.MacAddress
                SwitchName  = $_.SwitchName
                Connected   = $_.Connected
                IPAddresses = @($_.IPAddresses)
            }
        })
        Disks           = @($vm | Get-VMHardDiskDrive | ForEach-Object {
            $vhd = $null
            try { $vhd = Get-VHD -Path $_.Path -ErrorAction SilentlyContinue } catch { }
            [PSCustomObject]@{
                Path         = $_.Path
                ControllerNo = $_.ControllerNumber
                Location     = $_.ControllerLocation
                Size         = if ($vhd) { $vhd.Size } else { $null }
                FileSize     = if ($vhd) { $vhd.FileSize } else { $null }
                VhdType      = if ($vhd) { $vhd.VhdType.ToString() } else { $null }
            }
        })
        Checkpoints     = @($vm | Get-VMSnapshot | ForEach-Object {
            [PSCustomObject]@{
                Id           = $_.Id.ToString()
                Name         = $_.Name
                CreationTime = $_.CreationTime.ToString('o')
                ParentId     = if ($_.ParentSnapshotId) { $_.ParentSnapshotId.ToString() } else { $null }
            }
        })
    }
} | ConvertTo-Json -Depth 6 -Compress
"""
```

**KVP 추출이 이 스크립트의 핵심이다.** `Msvm_KvpExchangeComponent.GuestIntrinsicExchangeItems`는
XML 문자열 배열이며, 각 항목에서 `Name`/`Data` 속성을 꺼내야 한다.

> **[검증 필요]** (§11-4): Linux 게스트는 `hyperv-daemons` 패키지(`hypervkvpd`)가 설치되어야 KVP를 제공한다.
> 대상 환경의 Linux VM에서 실측하여 어떤 키가 오는지 확인한다.

### 6.2 호스트 정보

```python
SCRIPT_HOST_INFO = r"""
$ErrorActionPreference = 'Stop'
$cs  = Get-CimInstance Win32_ComputerSystem
$bios= Get-CimInstance Win32_BIOS
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = @(Get-CimInstance Win32_Processor)
[PSCustomObject]@{
    Name           = $cs.Name
    Fqdn           = "$($cs.DNSHostName).$($cs.Domain)".TrimEnd('.')
    Vendor         = $cs.Manufacturer
    Model          = $cs.Model
    SerialNumber   = $bios.SerialNumber
    CpuModel       = $cpu[0].Name
    CpuSockets     = $cpu.Count
    CpuCores       = ($cpu | Measure-Object -Property NumberOfCores -Sum).Sum
    CpuMhz         = $cpu[0].MaxClockSpeed
    MemoryBytes    = $cs.TotalPhysicalMemory
    OsName         = $os.Caption
    OsVersion      = $os.Version
    OsBuild        = $os.BuildNumber
    LastBootUpTime = $os.LastBootUpTime.ToString('o')
    ManagementIp   = (Get-NetIPAddress -AddressFamily IPv4 |
                      Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } |
                      Select-Object -First 1 -ExpandProperty IPAddress)
    Nics           = @(Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object {
        [PSCustomObject]@{ Name=$_.Name; Mac=$_.MacAddress; SpeedBps=$_.LinkSpeed; Status=$_.Status }
    })
} | ConvertTo-Json -Depth 4 -Compress
"""
```

### 6.3 클러스터 노드 (클러스터 연결 시)

```python
SCRIPT_CLUSTER_NODES = r"""
$ErrorActionPreference = 'Stop'
Import-Module FailoverClusters -ErrorAction Stop
$cluster = Get-Cluster
[PSCustomObject]@{
    ClusterName = $cluster.Name
    Nodes = @(Get-ClusterNode | ForEach-Object {
        [PSCustomObject]@{ Name=$_.Name; State=$_.State.ToString(); Id=$_.Id }
    })
} | ConvertTo-Json -Depth 4 -Compress
"""
```

> **[검증 필요]** (§11-6): `FailoverClusters` 모듈 가용성과 클러스터 이름으로의 원격 조회 가능 여부.
> 클러스터 이름에 접속하면 임의 노드로 라우팅되므로, 노드 목록을 얻은 뒤 **노드별로 개별 접속**해야
> 각 노드의 VM을 모두 수집할 수 있다.

### 6.4 스토리지 (CSV / SMB / 로컬)

```python
SCRIPT_STORAGE = r"""
$ErrorActionPreference = 'Continue'
$result = @()
try {
    Import-Module FailoverClusters -ErrorAction Stop
    $result += Get-ClusterSharedVolume | ForEach-Object {
        $info = $_.SharedVolumeInfo[0]
        [PSCustomObject]@{
            Kind = 'csv'; Name = $_.Name
            Path = $info.FriendlyVolumeName
            Capacity = $info.Partition.Size
            Free = $info.Partition.FreeSpace
        }
    }
} catch { }
$result += Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.Size -gt 0 } | ForEach-Object {
    [PSCustomObject]@{
        Kind='local'; Name=$_.FileSystemLabel; Path=$_.Path
        Capacity=$_.Size; Free=$_.SizeRemaining
    }
}
$result | ConvertTo-Json -Depth 4 -Compress
"""

SCRIPT_SWITCHES = r"""
Get-VMSwitch | ForEach-Object {
    [PSCustomObject]@{
        Id=$_.Id.ToString(); Name=$_.Name; SwitchType=$_.SwitchType.ToString()
        NetAdapter=$_.NetAdapterInterfaceDescription
    }
} | ConvertTo-Json -Depth 3 -Compress
"""
```

---

## 7. 매핑 (`mapper.py`)

### 7.1 전원 상태 — 정수값 우선

로케일에 따라 문자열이 달라지므로 **CIM 정수값을 쓴다** (계획 02 §9.1).

```python
def map_power_state(state: int | str | None) -> PowerState:
    if isinstance(state, int):
        return HYPERV_ENABLED_STATE_MAP.get(state, PowerState.UNKNOWN)
    # 폴백: 문자열 (영문 로케일 가정)
    return {"Running": PowerState.ON, "Off": PowerState.OFF,
            "Saved": PowerState.SUSPENDED, "Paused": PowerState.SUSPENDED}.get(
                str(state), PowerState.UNKNOWN)
```

### 7.2 게스트 정보 (FR-501)

```python
def map_guest_info(row: dict, observed_at: datetime) -> GuestInfo:
    if not row.get("IntegrationOk"):
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    v4_raw = _split_kvp_addresses(row.get("KvpIPv4"))
    v6_raw = _split_kvp_addresses(row.get("KvpIPv6"))
    # 어댑터에서 얻은 IP도 병합 (KVP가 비어도 Get-VMNetworkAdapter가 줄 수 있음)
    for ad in row.get("Adapters") or []:
        v4_raw.extend(ad.get("IPAddresses") or [])

    v4, v6 = split_ip_families(v4_raw + v6_raw)      # 계획 02 §9.3
    fqdn = row.get("KvpFQDN")
    os_name = row.get("KvpOSName")

    if not fqdn and not os_name and not v4 and not v6:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_version=row.get("KvpOSVersion"),
        os_source=OsSource.GUEST_TOOLS if os_name else None,
        hostname=fqdn,
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        observed_at=observed_at,
    )


def _split_kvp_addresses(raw: str | None) -> list[str]:
    """KVP의 NetworkAddressIPv4는 세미콜론 구분 문자열로 오는 경우가 있다."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[;,]", raw) if p.strip()]
```

**Hyper-V에는 VM 구성값 OS가 없다.** 게스트 OS는 KVP가 유일한 출처이므로,
KVP가 없으면 OS를 알 수 없다 (vCenter의 `config.guestFullName` 같은 대체가 없음).

### 7.3 VM 매핑

```python
def map_virtual_machine(connection_id: UUID, row: dict, observed_at: datetime) -> VirtualMachine:
    generation = row.get("Generation")
    return VirtualMachine(
        resource_id=uuid4(),
        connection_id=connection_id,
        native_id=str(row["Id"]),                    # VM GUID
        name=row.get("Name") or str(row["Id"]),
        bios_uuid=_normalize_guid(row.get("BiosGuid")),
        power_state=map_power_state(row.get("State")),
        connection_state=ConnectionState.CONNECTED,
        boot_time=_boot_time_from_uptime(row.get("Uptime"), observed_at),
        cpu=CpuSpec(total_vcpu=int(row.get("ProcessorCount") or 0)),
        memory=MemorySpec(
            assigned_mb=int((row.get("MemoryAssigned") or row.get("MemoryStartup") or 0) // 1_048_576),
            dynamic_enabled=bool(row.get("DynamicMemory")),
            dynamic_min_mb=_to_mb(row.get("MemoryMinimum")),
            dynamic_max_mb=_to_mb(row.get("MemoryMaximum")),
        ),
        platform=PlatformSpec(
            hardware_version=row.get("Version"),
            firmware=Firmware.UEFI if generation == 2 else Firmware.BIOS,
            configured_os=None,                      # Hyper-V는 구성값 OS 없음
            generation=generation,
        ),
        guest=map_guest_info(row, observed_at),
        disks=tuple(_map_disk(d) for d in (row.get("Disks") or [])),
        adapters=tuple(_map_adapter(a) for a in (row.get("Adapters") or [])),
        snapshots=_map_checkpoints(row.get("Checkpoints") or []),
        host_native_id=row.get("ComputerName"),
        cluster_native_id=None,                      # 클러스터 연결 시 후처리로 채움
        resource_pool=None,                          # 미지원
        folder_path=None,                            # 미지원
        annotation=row.get("Notes"),
        created_at=_parse_iso(row.get("CreationTime")),
        last_seen_at=observed_at,
    )


def _map_disk(d: dict) -> VirtualDisk:
    vhd_type = (d.get("VhdType") or "").lower()
    return VirtualDisk(
        key=f"{d.get('ControllerNo')}:{d.get('Location')}",
        label=None,
        provisioned_bytes=int(d.get("Size") or 0),          # VHD 최대 크기
        used_bytes=int(d["FileSize"]) if d.get("FileSize") is not None else None,
        provisioning=(
            DiskProvisioning.THIN if vhd_type == "dynamic"
            else DiskProvisioning.THICK if vhd_type == "fixed"
            else DiskProvisioning.UNKNOWN
        ),
        datastore_name=_volume_from_path(d.get("Path")),
        file_path=d.get("Path"),
    )
```

**Hyper-V는 vCenter와 달리 실제 사용량(`FileSize`)을 얻을 수 있다.** VHDX 파일 크기가 곧 실사용량이다.

---

## 8. 클러스터 노드 순회 (부분 실패 — FR-204)

```python
async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
    observed_at = datetime.now(UTC)
    nodes = await self._resolve_nodes()          # 단독 호스트면 [자기 자신]
    total, failed_nodes = 0, []

    for node in nodes:
        try:
            rows = await self._runner_for(node).invoke_json(SCRIPT_LIST_VMS)
        except AuthenticationError:
            raise                                 # 자격증명 문제 — 전체 중단
        except PortalError as exc:
            failed_nodes.append((node, str(exc)))
            logger.warning("노드 수집 실패", extra={"node": node, "error": str(exc)})
            continue                              # 다른 노드는 계속
        for row in rows:
            total += 1
            yield map_virtual_machine(self._conn.connection_id, row, observed_at)

    self._outcomes.append(CollectionOutcome(
        resource_type=ResourceType.VIRTUAL_MACHINE,
        collected_count=total,
        failed=bool(failed_nodes) and total == 0,      # 전부 실패한 경우만 failed
        error=(f"{len(failed_nodes)}개 노드 수집 실패: "
               f"{', '.join(n for n, _ in failed_nodes)}") if failed_nodes else None,
    ))
```

**일부 노드만 실패하면 `failed=False`로 둔다.** 그래야 수집된 VM이 저장되고,
동시에 `error`에 실패 노드가 기록되어 관리자가 인지한다.

> **주의**: 일부 노드가 실패했는데 `mark_missing`을 호출하면 그 노드의 VM이 전부 미발견 처리된다.
> 워커는 `outcome.error`가 있으면 `mark_missing`을 건너뛰어야 한다 (계획 06 §11).

---

## 9. 연결 테스트 (FR-106)

```python
async def check_connection(self) -> ConnectionCheckResult:
    runner = StageRunner()
    await runner.run(CheckStage.REACHABLE, self._check_port)
    await runner.run(CheckStage.TLS_VALID, self._check_tls)      # HTTP면 통과 + 경고
    await runner.run(CheckStage.AUTHENTICATED, self._check_auth)

    readable: set[ResourceType] = set()
    await runner.run(CheckStage.AUTHORIZED, lambda: self._check_authorized(readable))
    return ConnectionCheckResult(stages=runner.results, readable_types=frozenset(readable),
                                server_version=self._os_version)


SCRIPT_PROBE_AUTH = r"$PSVersionTable.PSVersion.ToString()"

SCRIPT_PROBE_PERMISSIONS = r"""
$r = @{}
try { $null = Get-VM | Select-Object -First 1; $r['vm'] = $true } catch { $r['vm'] = $false }
try { $null = Get-VMSwitch | Select-Object -First 1; $r['network'] = $true } catch { $r['network'] = $false }
try { $null = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
             -ErrorAction Stop | Select-Object -First 1; $r['wmi'] = $true } catch { $r['wmi'] = $false }
$r | ConvertTo-Json -Compress
"""
```

**인증 방식이 잘못되면 AUTHENTICATED 단계에서 걸러진다.** 실패 시 시도한 인증 방식을 `detail`에 포함하여
관리자가 조정할 수 있게 한다 (자격증명은 제외).

---

## 10. 예외 변환 (`errors.py`)

```python
def translate_error(exc: Exception) -> PortalError:
    msg = str(exc)

    # pypsrp/requests 계열 인증 실패 — 다양한 형태로 온다
    if isinstance(exc, AuthenticationError_pypsrp) or "401" in msg or "unauthorized" in msg.lower():
        return AuthenticationError("인증에 실패했습니다. 계정, 비밀번호, 인증 방식을 확인하세요.")
    if "access is denied" in msg.lower() or "5" == getattr(exc, "code", None):
        return PermissionError("원격 관리 권한이 부족합니다.")
    if isinstance(exc, (socket.timeout, asyncio.TimeoutError, TimeoutError)):
        return UnreachableError("응답 시간이 초과되었습니다.")
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, OSError)):
        return UnreachableError("WinRM 서비스에 연결할 수 없습니다. 포트와 방화벽을 확인하세요.")
    if isinstance(exc, ssl.SSLError):
        return UnreachableError("TLS 인증서 검증에 실패했습니다.")
    if "cannot find the type" in msg.lower() or "is not recognized" in msg.lower():
        return CollectionError("필요한 PowerShell 모듈이 없습니다 (Hyper-V / FailoverClusters).")
    return CollectionError(f"Hyper-V 수집 오류: {sanitize_message(msg)}")
```

**WinRM은 인증 실패를 여러 형태로 보고한다.** 문자열 매칭에 의존하는 부분이 있으므로
**모호하면 재시도하지 않는 쪽(`AuthenticationError`)으로 판정한다.** 계정 잠금이 재시도 지연보다 훨씬 큰 피해다.

> **[검증 필요]**: `pypsrp`가 실제로 던지는 인증 예외 타입을 확인하여 문자열 매칭을 타입 매칭으로 교체한다.

---

## 11. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 0 | **§11 미검증 항목 확인** (pypsrp 인증·비동기, KVP Linux, FailoverClusters) | 결과를 `docs/00_research_notes.md`에 반영 |
| 1 | `errors.py` | 인증 실패 → `AuthenticationError(retryable=False)` |
| 2 | `session.py` 인증 분기 | NTLM/Kerberos 연결, 실패 시 세션 정리 |
| 3 | `runner.py` + `parse_ps_json` | **단일 항목이 배열로 정규화**, 빈 출력, 깨진 JSON |
| 4 | `SCRIPT_LIST_VMS` | 실제 Hyper-V에서 실행하여 출력 캡처 → 테스트 fixture로 커밋 |
| 5 | KVP 파싱 | XML 항목에서 Name/Data 추출, 세미콜론 IP 분리 |
| 6 | `mapper.py` 게스트 정보 | 통합 서비스 미동작 VM 분기 |
| 7 | `mapper.py` VM/디스크/어댑터 | 정수 EnabledState 매핑, MAC 정규화, VHD 타입 |
| 8 | 호스트·스위치·스토리지 매핑 | 용량 바이트 통일 |
| 9 | 클러스터 노드 순회 | **노드 1개 실패 시 나머지 수집 계속** |
| 10 | `reader.py` | **계약 테스트 스위트(계획 03 §9) 통과** |

## 12. 완료 기준

- [ ] `arch_check.py` 통과 — vcenter 미참조, 읽기 전용 메서드만
- [ ] 계약 테스트 스위트 14종 통과 (04와 동일 스위트)
- [ ] `parse_ps_json`이 단일 객체·배열·빈 출력을 모두 처리
- [ ] NTLM·Kerberos 인증 방식이 설정으로 선택되고 동작
- [ ] KVP 미제공 VM이 `TOOLS_NOT_INSTALLED`/`TOOLS_NOT_RUNNING`으로 매핑
- [ ] KVP의 세미콜론 구분 IP가 분리·정규화됨
- [ ] 링크로컬·루프백 IP가 결과에 없음
- [ ] 전원 상태가 정수 `EnabledState` 기준으로 매핑 (로케일 무관)
- [ ] 클러스터 연결에서 노드 순회, 일부 실패 시 부분 성공 + error 기록
- [ ] WinRM/WMI 예외가 어댑터 밖으로 나오지 않음
- [ ] **상태 변경 cmdlet이 스크립트에 없음**: `Set-VM`, `Start-VM`, `Stop-VM`, `Remove-VM`, `Checkpoint-VM`, `Restore-VMSnapshot`, `New-VM`, `Rename-VM`, `Move-VM`

## 13. 주의사항

- **상태 변경 cmdlet 금지.** PowerShell은 문자열이라 arch-check가 잡지 못한다. `scripts.py`를 verifier가 직접 검토하고, 다음으로 확인한다:
  ```bash
  grep -nE "(Set|Start|Stop|Remove|New|Rename|Move|Restore|Checkpoint|Export|Import)-VM" src/infrastructure/hyperv/
  ```
  `Get-VM`, `Get-VMSnapshot` 등 조회 cmdlet만 존재해야 한다.
- **원격 PowerShell은 느리다.** 호스트당 호출을 최소화하고, `Get-VHD`처럼 VM 수만큼 반복되는 호출은 성능을 재확인한다. 필요하면 디스크 상세를 별도 옵션으로 분리한다.
- `ConvertTo-Json`의 단일 항목 문제(§5.1)와 `-Depth` 부족을 주의한다.
- 날짜는 스크립트에서 `.ToString('o')`로 ISO 8601 문자열화한다. `/Date(...)/` 파싱을 피한다.
- 클러스터 이름 접속은 임의 노드로 라우팅된다. **노드별 개별 접속**이 필요하다 (§6.3).
- SCVMM 연동은 CST-09 확정 전까지 구현하지 않는다.
- 개발·테스트는 목 커넥터로 한다 (CST-04).
