# 05. Hyper-V 수집 어댑터

> Wave: 2 · 계층: infrastructure (`src/infrastructure/hyperv/`)
> 담당 요건: FR-103, FR-106, FR-301, FR-501, `spec.md` §2.2 Hyper-V 출처 열, CST-03·06·09
> 의존: 02, 03 · 관련 결정: D-003, D-005, D-008, **D-012**

## 1. 목적

WinRM(PowerShell Remoting) / WMI로 Hyper-V 인벤토리를 수집하여 공통 도메인 모델로 반환한다.

Microsoft 환경의 관리 콘솔은 **SCVMM**과 **Hyper-V 관리자**(Hyper-V Manager / 장애 조치 클러스터 관리자) 두 계열이며,
**어느 쪽이 있느냐에 따라 수집 경로가 달라진다.** 이 계획은 두 경로를 모두 구현한다 (D-012).

**`src/infrastructure/vcenter`를 절대 import하지 않는다** (arch-check 특화 규칙 7).
두 경로는 같은 패키지 안에 두어 WinRM 세션·실행기·예외 변환을 공유한다 (§3).

## 2. 두 개의 관리 콘솔, 두 개의 수집 경로

`docs/00_research_notes.md` §7.2: Hyper-V 관리자 계열에는 중앙 관리 지점이 없고, SCVMM에는 있다.

| 항목 | 경로 A — Hyper-V 관리자 | 경로 B — SCVMM |
|---|---|---|
| 대응 콘솔 | Hyper-V Manager, 장애 조치 클러스터 관리자 | System Center Virtual Machine Manager |
| 연결 단위 | **호스트 1대 또는 클러스터 1개 = 연결 1개** | **SCVMM 서버 1대 = 연결 1개** (fabric 전체) |
| `ConnectionKind` | `hyperv-host` · `hyperv-cluster` | `scvmm` |
| 접속 대상 | Hyper-V 호스트 / 클러스터 노드 | SCVMM 관리 서버 |
| PowerShell 모듈 | `Hyper-V`, `FailoverClusters` | `VirtualMachineManager` |
| 조회 위상 | **호스트별로 순회** — 노드 수만큼 호출 | **1회 호출로 전체** — vCenter와 유사 |
| 게스트 정보 | KVP를 직접 읽음 (실시간) | **VMM DB의 캐시값** — 신선도 별도 문제 (§7.5) |
| 읽기 전용 계정 | **어렵다** — `Hyper-V Administrators`는 전원·삭제 권한 포함 (§4.3) | **가능** — VMM `Read-Only Administrator` 역할 |
| 규모 적합성 | 호스트 수십 대까지 | 수백 대 이상 |

**vCenter와의 관계로 보면 경로 B가 vCenter에 가깝고, 경로 A가 이질적이다.**

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 관리 지점 | vCenter 1대가 전체 보유 | 호스트마다 자기 VM만 앎 | SCVMM이 전체 보유 |
| 조회 | PropertyCollector 일괄 | 호스트별 PowerShell/WMI | SCVMM cmdlet 일괄 |
| 인증 | 사용자명+비밀번호 | **NTLM/Kerberos/CredSSP 선택** | 동일 |
| 성능 | API 호출 빠름 | **원격 PowerShell은 훨씬 느림** | 호출 1회지만 fabric 전체라 응답이 큼 |

경로 A에서는 **호스트당 호출 횟수 최소화**가 설계를 지배한다. 여러 정보를 한 스크립트로 모아 조회한다.
경로 B에서는 **응답 크기와 VMM 서버 부하**가 관건이다. 필요한 속성만 `Select-Object`로 잘라 반환한다.

### 2.1 두 경로를 동시에 등록하면 중복 수집된다

같은 VM이 SCVMM 연결과 호스트 직접 연결 양쪽에서 보인다. `connection_id`가 다르므로
CI 식별 1순위 키가 갈려 **별도 자원으로 생성된다** (D-006). 2순위(BIOS UUID) 매칭으로 중복 후보가
감지되지만 자동 병합하지 않는다.

> **SCVMM이 있는 환경에서는 SCVMM만 등록한다.** 호스트를 함께 등록하는 것은
> SCVMM이 관리하지 않는 독립 호스트가 따로 있을 때뿐이다. UI 등록 화면에 이 경고를 표시한다 (계획 11).

## 3. 모듈 구성

두 경로는 WinRM 세션·PowerShell 실행·예외 변환을 공유하고, **cmdlet 집합과 매핑만 갈린다.**

```
src/infrastructure/hyperv/
├── __init__.py          HyperVHostInventoryReader, ScvmmInventoryReader export
├── session.py           WinRM 세션, 인증 방식 분기          ← 공통
├── runner.py            PowerShell 실행 + JSON 파싱         ← 공통
├── errors.py            WinRM/WMI/VMM 예외 → 도메인 예외    ← 공통
├── normalize.py         전원 상태·MAC·IP·용량 정규화        ← 공통 (§7.1)
├── host_scripts.py      Hyper-V / FailoverClusters cmdlet   ← 경로 A
├── host_mapper.py                                           ← 경로 A
├── host_reader.py       hyperv-host · hyperv-cluster        ← 경로 A
├── scvmm_scripts.py     VirtualMachineManager cmdlet        ← 경로 B
├── scvmm_mapper.py                                          ← 경로 B
└── scvmm_reader.py      scvmm                               ← 경로 B
```

**별도 최상위 패키지(`src/infrastructure/scvmm/`)로 분리하지 않는다.**
분리하면 세션·실행기를 공유하기 위해 `hyperv`를 import해야 하는데, 이는 arch-check 특화 규칙 2
(어댑터 간 교차 참조 금지)에 걸린다. 둘은 같은 WinRM 스택을 쓰는 **한 어댑터의 두 수집 경로**다.

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

### 4.2 SCVMM 접속 대상 — 이중 홉을 만들지 않는다

경로 B에서 WinRM으로 접속하는 대상은 **SCVMM 관리 서버 자신**이다.
그 위에서 `Get-SCVMMServer -ComputerName localhost`로 로컬 연결하면 자격증명 위임이 발생하지 않는다.

```
[포탈] --WinRM--> [SCVMM 서버] --로컬--> [VMM DB / fabric]        권장. 이중 홉 없음
[포탈] --WinRM--> [콘솔 설치 서버] --원격--> [SCVMM 서버]          CredSSP 필요. 피한다
```

콘솔만 설치된 별도 서버를 경유하면 **두 번째 홉에서 자격증명 위임이 필요해 CredSSP를 켜야 한다.**
연결 등록 시 주소로 SCVMM 서버를 직접 지정하도록 UI 도움말에 명시한다.

> **[검증 필요]**: `VirtualMachineManager` 모듈은 SCVMM 서버에 기본 설치되지만,
> 원격 PowerShell 세션에서 모듈 로드와 `Get-SCVMMServer`가 성공하는지 실환경에서 확인한다.
> SCVMM 2019/2022의 모듈 경로와 `-ErrorAction Stop` 동작을 함께 본다.

### 4.3 읽기 전용 계정 (NFR-201, D-005) — 접속 권한과 조회 권한을 나눠서 본다

접속 계정이 대상 서버의 관리자여서는 안 된다. **WinRM 접속 권한**과 **대상 시스템 조회 권한**은 별개다.

| | 계층 1 — WinRM 접속 | 계층 2 — 조회 권한 |
|---|---|---|
| **경로 B** | SCVMM 서버의 `Remote Management Users` (**로컬 관리자 아님**) | VMM User Role `Read-Only Administrator` |
| **경로 A** | **JEA 제약 세션** — 가상 계정으로 실행 | JEA `VisibleFunctions`로 수집 함수만 노출 |

**경로 B의 계층 1을 빠뜨리기 쉽다.** VMM 역할이 Read-Only여도 접속 계정이 SCVMM 서버의
로컬 관리자면 그 서버에서 무엇이든 할 수 있다. **VMM 역할은 VMM 안에서만 유효하다.**

#### 4.3.1 경로 A는 JEA를 쓴다 — 다만 스크립트를 그대로 보낼 수 없다

`Get-VM` 등 `Hyper-V` 모듈 cmdlet은 `Hyper-V Administrators` 권한을 요구하는데,
이 그룹은 VM 생성·삭제·전원 제어를 **함께** 갖는다. 그대로 쓰면 D-005의 1번 방어(권한 차단)가 무너진다.

JEA는 이 문제를 위한 표준 기능이지만, **제약 세션의 언어 모드가 걸림돌이다.**

| 언어 모드 | §6 스크립트 실행 가능 여부 |
|---|---|
| `NoLanguage` (RestrictedRemoteServer 기본) | **불가** — 스크립트 블록 자체가 실행되지 않는다 |
| `ConstrainedLanguage` | **불가** — `[PSCustomObject]`는 되지만 KVP 파싱의 **`[xml]` 캐스팅이 허용 타입이 아니다** |

따라서 **스크립트를 세션으로 보내는 방식(§5.1)은 JEA에서 동작하지 않는다.**
대신 **역할 기능 파일에 수집 함수를 정의하고 그 함수만 노출**한다. 역할 기능 파일의 함수는
정의 시점에 신뢰되므로 언어 모드 제약을 받지 않는다.

```powershell
# WzonePortalReadOnly.psrc — 대상 호스트에 배포
FunctionDefinitions = @(
    @{ Name = 'Get-WzoneVmInventory';  ScriptBlock = { <§6.1 본문> } }   # ConvertTo-Json까지 수행
    @{ Name = 'Get-WzoneHostInfo';     ScriptBlock = { <§6.2 본문> } }
    @{ Name = 'Get-WzoneClusterNodes'; ScriptBlock = { <§6.3 본문> } }
    @{ Name = 'Get-WzoneStorage';      ScriptBlock = { <§6.4 본문> } }
)
VisibleFunctions = 'Get-Wzone*'
```

```powershell
# WzonePortalReadOnly.pssc
SessionType         = 'RestrictedRemoteServer'
RunAsVirtualAccount = $true
RoleDefinitions     = @{ 'DOMAIN\WzonePortalReaders' = @{ RoleCapabilities = 'WzonePortalReadOnly' } }
```

**어댑터 쪽 영향**: `host_scripts.py`의 상수는 **JEA 미사용 환경(개발·검증)용으로 남기고**,
JEA 환경에서는 함수 이름만 호출한다. 연결 설정에 세션 구성 이름을 두어 갈린다.

```python
# session.py — 세션 구성 이름이 있으면 JEA 엔드포인트로 붙는다
RunspacePool(self._wsman, configuration_name=self._conn.session_configuration or "Microsoft.PowerShell")
```

```python
# runner 호출부
script = "Get-WzoneVmInventory" if self._conn.session_configuration else SCRIPT_LIST_VMS
```

> **[검증 필요]** (`docs/00_research_notes.md` §11-17·18)
> - `pypsrp`의 `RunspacePool`이 `configuration_name`을 지원하는지. **불가하면 경로 A의 접속 계층을 다시 정해야 한다**
> - JEA 함수의 반환값 직렬화 — 함수 안에서 `ConvertTo-Json`까지 마쳐 **문자열을 반환**하면
>   역직렬화 문제를 피할 수 있다. 실제 동작을 확인한 뒤 함수 시그니처를 확정한다

#### 4.3.2 그 전에 — SCVMM 편입을 먼저 검토한다

JEA 구성은 호스트마다 배포·유지해야 하고, §6 스크립트를 고치면 **호스트의 역할 기능 파일도 갱신 대상**이 된다.
**대부분의 경우 그 호스트를 SCVMM에 등록하는 편이 비용이 낮다** (D-012 결정 8).

```
독립 Hyper-V 호스트 발견
   └─ SCVMM에 편입 가능한가?
        ├─ 예   → 편입한다. 경로 A를 쓰지 않는다        ← 우선
        └─ 아니오 (격리망·DMZ·검증 전용)
              └─ 경로 A + JEA로 등록
```

**JEA 구축 전에는 해당 호스트를 등록하지 않는다.** 수집 공백이 생기더라도,
전원·삭제 권한을 가진 자격증명을 포탈이 보관하는 것보다 낫다.

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

## 6. 수집 스크립트 — 경로 A (`host_scripts.py`)

Hyper-V 관리자 계열 환경. `Hyper-V`·`FailoverClusters` 모듈을 쓴다.
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

## 7. 수집 스크립트 — 경로 B (`scvmm_scripts.py`)

SCVMM 서버 1회 접속으로 fabric 전체를 얻는다. **호스트 순회가 없다.**

### 7.1 공통 서두 — 모듈 로드와 서버 연결

```python
SCVMM_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
Import-Module VirtualMachineManager -ErrorAction Stop
$vmm = Get-SCVMMServer -ComputerName localhost   # §4.2 — 이중 홉을 만들지 않는다
"""
```

모든 SCVMM 스크립트는 이 서두를 앞에 붙여 실행한다.
`-VMMServer $vmm`을 각 cmdlet에 명시하여 기본 연결에 의존하지 않는다.

### 7.2 VM 목록

```python
SCRIPT_SCVMM_LIST_VMS = SCVMM_PREAMBLE + r"""
Get-SCVirtualMachine -VMMServer $vmm -All | ForEach-Object {
    $vm = $_
    [PSCustomObject]@{
        VmmId           = $vm.ID.ToString()          # VMM 객체 ID — 사용하지 않는다 (§8.4)
        Id              = $vm.VMId.ToString()        # Hyper-V VM GUID — native_id
        Name            = $vm.Name
        State           = $vm.VirtualMachineState.ToString()
        Status          = $vm.StatusString
        Generation      = $vm.Generation
        Version         = $vm.Version
        ProcessorCount  = $vm.CPUCount
        MemoryMB        = $vm.Memory
        DynamicMemory   = $vm.DynamicMemoryEnabled
        DynamicMinMB    = $vm.DynamicMemoryMinimumMB
        DynamicMaxMB    = $vm.DynamicMemoryMaximumMB
        CreationTime    = if ($vm.CreationTime) { $vm.CreationTime.ToString('o') } else { $null }
        ModifiedTime    = if ($vm.ModifiedTime) { $vm.ModifiedTime.ToString('o') } else { $null }
        Description     = $vm.Description
        Owner           = $vm.Owner.Name             # VMM 소유자 — 포탈 메타데이터와 다름 (§8.6)
        HostName        = $vm.VMHost.Name
        HostGroupPath   = $vm.VMHostGroupPath
        ClusterName     = $vm.VMHost.VMHostCluster.Name
        IsHighlyAvailable = $vm.IsHighlyAvailable
        OperatingSystem = $vm.OperatingSystem.Name   # VMM DB 값 — 구성값 취급 (§8.5)
        Adapters        = @($vm.VirtualNetworkAdapters | ForEach-Object {
            [PSCustomObject]@{
                Id          = $_.ID.ToString()
                MacAddress  = $_.MACAddress
                SwitchName  = $_.VirtualNetwork
                VMNetwork   = $_.VMNetwork.Name
                Connected   = $_.Enabled
                IPv4        = @($_.IPv4Addresses)
                IPv6        = @($_.IPv6Addresses)
            }
        })
        Disks           = @($vm.VirtualDiskDrives | ForEach-Object {
            $vhd = $_.VirtualHardDisk
            [PSCustomObject]@{
                Path         = if ($vhd) { $vhd.Location } else { $null }
                ControllerNo = $_.BusType.ToString() + $_.Bus
                Location     = $_.Lun
                Size         = if ($vhd) { $vhd.MaximumSize } else { $null }
                FileSize     = if ($vhd) { $vhd.Size } else { $null }
                VhdType      = if ($vhd) { $vhd.VHDType.ToString() } else { $null }
                Datastore    = if ($vhd) { $vhd.HostVolume.Name } else { $null }
            }
        })
        Checkpoints     = @(Get-SCVMCheckpoint -VMMServer $vmm -VM $vm | ForEach-Object {
            [PSCustomObject]@{
                Id           = $_.ID.ToString()
                Name         = $_.Name
                CreationTime = $_.AddedTime.ToString('o')
                ParentId     = if ($_.ParentCheckpointID) { $_.ParentCheckpointID.ToString() } else { $null }
            }
        })
    }
} | ConvertTo-Json -Depth 6 -Compress
"""
```

> **[검증 필요]** — 실환경 없이 닫히지 않는 항목이다. 착수 전 확인하고 `docs/00_research_notes.md` §11에 반영한다.
> - `VMId`가 모든 VM에 존재하는지 (VMM에만 등록되고 배포되지 않은 VM은 비어 있을 수 있다)
> - `IPv4Addresses`가 실제로 채워지는지, 채워진다면 출처가 KVP인지 VMM IP 풀인지
> - `OperatingSystem.Name`의 갱신 시점 — VM 생성 시 지정값인지 에이전트가 갱신하는지 (§8.5의 전제)
> - `Get-SCVMCheckpoint`를 VM별로 호출하면 VM 수만큼 왕복한다. **VM 수백 대 환경에서 실측**하고,
>   느리면 체크포인트를 별도 스크립트로 분리한다 (`Get-SCVMCheckpoint -VMMServer $vmm` 전량 조회 후 VM별 그룹핑)

### 7.3 호스트·클러스터

```python
SCRIPT_SCVMM_HOSTS = SCVMM_PREAMBLE + r"""
Get-SCVMHost -VMMServer $vmm | ForEach-Object {
    [PSCustomObject]@{
        Id             = $_.ID.ToString()
        Name           = $_.Name
        Fqdn           = $_.FQDN
        ComputerState  = $_.ComputerState.ToString()
        OverallState   = $_.OverallState.ToString()
        InMaintenance  = $_.MaintenanceHost
        Vendor         = $_.ComputerManufacturer
        Model          = $_.ComputerModel
        CpuModel       = $_.ProcessorName
        CpuSockets     = $_.PhysicalCPUCount
        CpuCores       = $_.CoresPerCPU * $_.PhysicalCPUCount
        LogicalCpus    = $_.LogicalProcessorCount
        MemoryBytes    = $_.TotalMemory
        HypervisorVer  = $_.HyperVVersion
        ClusterName    = $_.VMHostCluster.Name
        HostGroupPath  = $_.VMHostGroup.Path
    }
} | ConvertTo-Json -Depth 4 -Compress
"""

SCRIPT_SCVMM_CLUSTERS = SCVMM_PREAMBLE + r"""
Get-SCVMHostCluster -VMMServer $vmm | ForEach-Object {
    [PSCustomObject]@{
        Id          = $_.ID.ToString()
        Name        = $_.Name
        ClusterName = $_.ClusterName
        NodeCount   = @($_.Nodes).Count
        Nodes       = @($_.Nodes | ForEach-Object { $_.Name })
    }
} | ConvertTo-Json -Depth 4 -Compress
"""
```

### 7.4 스토리지·네트워크

```python
SCRIPT_SCVMM_STORAGE = SCVMM_PREAMBLE + r"""
$result = @()
$result += Get-SCStorageVolume -VMMServer $vmm | ForEach-Object {
    [PSCustomObject]@{
        Id=$_.ID.ToString(); Name=$_.Name
        Kind=if ($_.IsClusterSharedVolume) { 'csv' } else { 'local' }
        Path=$_.MountPoints -join ';'
        Capacity=$_.Capacity; Free=$_.FreeSpace
        HostName=$_.VMHost.Name
    }
}
$result += Get-SCStorageFileShare -VMMServer $vmm | ForEach-Object {
    [PSCustomObject]@{
        Id=$_.ID.ToString(); Name=$_.Name; Kind='smb'; Path=$_.SharePath
        Capacity=$_.Capacity; Free=$_.FreeSpace; HostName=$null
    }
}
$result | ConvertTo-Json -Depth 4 -Compress
"""

SCRIPT_SCVMM_NETWORKS = SCVMM_PREAMBLE + r"""
Get-SCVMNetwork -VMMServer $vmm | ForEach-Object {
    [PSCustomObject]@{
        Id=$_.ID.ToString(); Name=$_.Name
        LogicalNetwork=$_.LogicalNetwork.Name
        IsolationType=$_.IsolationType.ToString()
    }
} | ConvertTo-Json -Depth 3 -Compress
"""
```

**SCVMM 고유 개념(Cloud, 호스트 그룹, 논리 네트워크, 서비스 템플릿)은 수집하지 않는다.**
공통 도메인 모델에 대응이 없고, 억지로 매핑하면 vCenter 자원과 같은 표에 놓을 수 없다 (FR-1203).
`HostGroupPath`는 참고용으로만 담고, 조회 필터로 쓰지 않는다.

### 7.5 대량 환경 대비

`Get-SCVirtualMachine -All`은 fabric 전체를 한 번에 반환한다. 응답이 커지면 VMM 서버 메모리와
WinRM 전송 시간이 함께 늘어난다. **호스트 그룹 단위 분할 조회**가 필요할 수 있다.

```powershell
Get-SCVMHostGroup -VMMServer $vmm | ForEach-Object { Get-SCVirtualMachine -VMHostGroup $_ }
```

> Step 2 실측(ROADMAP §15.3)과 같은 방식으로 **VM 총 건수와 1회 조회 소요 시간을 먼저 측정**한 뒤
> 분할 여부를 정한다. 측정 전에 분할부터 구현하지 않는다.

---

## 8. 매핑 (`host_mapper.py` · `scvmm_mapper.py`)

### 8.1 전원 상태 — 경로별 출처가 다르다 (`normalize.py`)

경로 A는 CIM 정수값, 경로 B는 .NET 열거형 이름을 반환한다.
**둘 다 로케일 영향을 받지 않는다** — 경로 A는 숫자이고, 경로 B는 표시 문자열이 아닌 열거형 이름이기 때문이다.
로케일에 취약한 것은 `Get-VM`의 `Status` 같은 표시 문자열이며, 이 값은 쓰지 않는다 (계획 02 §9.1).

```python
def map_power_state(state: int | str | None) -> PowerState:
    if isinstance(state, int):                      # 경로 A — Msvm_ComputerSystem.EnabledState
        return HYPERV_ENABLED_STATE_MAP.get(state, PowerState.UNKNOWN)
    return SCVMM_STATE_MAP.get(str(state), PowerState.UNKNOWN)


SCVMM_STATE_MAP: dict[str, PowerState] = {       # 경로 B — VirtualMachineState 열거형
    "Running": PowerState.ON,
    "PowerOff": PowerState.OFF,
    "Stopped": PowerState.OFF,
    "Paused": PowerState.SUSPENDED,
    "Saved": PowerState.SUSPENDED,
}
```

> **[검증 필요]**: `VirtualMachineState`의 실제 열거형 값 전체 목록.
> 전이 상태(`Starting`, `Stopping`, `Saving`)가 어떻게 오는지 확인하고,
> 매핑되지 않는 값은 `UNKNOWN`으로 두되 **로그에 원본 값을 남겨** 누락을 발견할 수 있게 한다.

### 8.2 게스트 정보 — KVP 직접 판정 (경로 A, FR-501)

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

**경로 A의 Hyper-V에는 VM 구성값 OS가 없다.** 게스트 OS는 KVP가 유일한 출처이므로,
KVP가 없으면 OS를 알 수 없다 (vCenter의 `config.guestFullName` 같은 대체가 없음).
경로 B는 사정이 다르다 — §8.5.

### 8.3 VM 매핑 (경로 A)

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

### 8.4 `native_id`는 VMM 객체 ID가 아니라 VM GUID다 (경로 B) — CI 식별의 핵심

`Get-SCVirtualMachine`은 ID를 두 개 반환한다.

| 속성 | 정체 | 사용 |
|---|---|---|
| `ID` | **VMM DB의 객체 ID.** SCVMM을 재설치하거나 VM을 다시 등록하면 바뀔 수 있다 | **쓰지 않는다** |
| `VMId` | **Hyper-V VM GUID.** 호스트가 보유한 값과 동일하다 | **`native_id`로 쓴다** |

```python
native_id=str(row["Id"])          # 스크립트에서 VMId를 Id 키로 담아 보냈다 (§7.2)
```

**VMM 객체 ID를 쓰면 안 되는 이유가 두 가지다.**
하나는 위의 불안정성이고, 다른 하나는 **경로 A와 경로 B가 같은 VM에 같은 식별자를 주어야**
나중에 SCVMM 도입·제거로 연결 유형이 바뀌어도 중복 후보 감지(§2.1)가 동작하기 때문이다.

> `VMId`가 비어 있는 VM(VMM에 등록만 되고 호스트에 배포되지 않은 상태)은 **수집에서 제외**하고
> 수집 결과의 `error`에 건수를 남긴다. 식별자 없는 자원을 만들면 재수집마다 중복이 쌓인다.

### 8.5 게스트 정보와 OS 출처 (경로 B)

SCVMM은 KVP를 직접 읽지 않고 **VMM DB에 캐시된 값**을 준다. 두 가지가 따라온다.

**1. OS 출처는 보수적으로 판정한다.** `OperatingSystem.Name`은 VM 생성 시 지정값일 수 있으므로
확인 전까지 **구성값(`OsSource.VM_CONFIG`)으로 매핑**한다. UI에 `(구성값)`이 병기되어(FR-304)
사용자가 실측값이 아님을 알 수 있다.

```python
def map_scvmm_guest(row: dict, observed_at: datetime) -> GuestInfo:
    v4, v6 = split_ip_families(
        [ip for ad in row.get("Adapters") or [] for ip in (ad.get("IPv4") or []) + (ad.get("IPv6") or [])]
    )
    os_name = row.get("OperatingSystem")

    # IP도 OS도 없으면 통합 서비스가 동작하지 않는 것으로 본다 (FR-501)
    if not v4 and not v6 and not os_name:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_source=OsSource.VM_CONFIG if os_name else None,   # [검증 필요] — §7.2 참조
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        observed_at=observed_at,
    )
```

**2. 신선도가 두 겹이 된다.** 포탈의 마지막 수집 시각과, **VMM이 그 VM을 마지막으로 갱신한 시각**이
다르다. VMM 새로 고침이 멈춰 있으면 포탈은 최신 수집인데 값은 오래된 상태가 된다.

`ModifiedTime`을 `guest.observed_at`이 아니라 **수집 결과의 참고 값으로 별도 보관**하고,
포탈 수집 시각과 크게 벌어지면 경고한다.

> **`Read-SCVirtualMachine`으로 새로 고침을 강제하지 않는다.** 이 cmdlet은 VMM DB를 갱신하는
> **쓰기 동작**이며, 읽기 전용 원칙(D-005) 위반이다. VMM 새로 고침 주기는 VMM 관리자의 영역이다.

### 8.6 VMM `Owner`는 포탈 메타데이터가 아니다

SCVMM의 `Owner`는 VMM 자체 소유자 필드다. 포탈의 소유자 메타데이터(FR-601)와 **혼합하지 않는다.**
수집값이 포탈 입력값을 덮어쓰면 FR-602 위반이다 (D-006).

Step 8에서 메타데이터 초기값 제안에 활용할 수는 있으나, **자동 채움이 아니라 사용자 확인을 거친다.**
그때까지는 `annotation` 성격의 참고 값으로만 담는다.

---

## 9. 클러스터 노드 순회 — 경로 A 전용 (부분 실패 — FR-204)

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

**경로 B에는 이 순회가 없다.** SCVMM이 이미 fabric 전체를 알고 있으므로 호출은 1회이며,
부분 실패 단위도 노드가 아니라 **자원 유형**이다 (VM은 성공했지만 스토리지 조회가 실패하는 식).
`CollectionOutcome`을 자원 유형별로 남기는 것은 두 경로가 동일하다.

---

## 10. 연결 테스트 (FR-106)

4단계 판정(도달·TLS·인증·권한)은 두 경로가 같고, **권한 프로브만 다르다.**

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

# 경로 A — Hyper-V 모듈과 WMI 접근을 각각 확인한다 (§4.3의 권한 문제를 여기서 드러낸다)
SCRIPT_PROBE_PERMISSIONS = r"""
$r = @{}
try { $null = Get-VM | Select-Object -First 1; $r['vm'] = $true } catch { $r['vm'] = $false }
try { $null = Get-VMSwitch | Select-Object -First 1; $r['network'] = $true } catch { $r['network'] = $false }
try { $null = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
             -ErrorAction Stop | Select-Object -First 1; $r['wmi'] = $true } catch { $r['wmi'] = $false }
$r | ConvertTo-Json -Compress
"""

# 경로 B — 모듈 로드·서버 연결·역할까지 한 번에 확인한다
SCRIPT_PROBE_SCVMM = r"""
$r = @{}
try {
    Import-Module VirtualMachineManager -ErrorAction Stop
    $vmm = Get-SCVMMServer -ComputerName localhost -ErrorAction Stop
    $r['module'] = $true
    $r['version'] = $vmm.ProductVersion
    $r['role'] = (Get-SCUserRole -VMMServer $vmm |
                  Where-Object { $_.Members -contains $vmm.UserName } |
                  Select-Object -First 1 -ExpandProperty Profile) -as [string]
    try { $null = Get-SCVirtualMachine -VMMServer $vmm | Select-Object -First 1; $r['vm'] = $true }
    catch { $r['vm'] = $false }
    try { $null = Get-SCVMHost -VMMServer $vmm | Select-Object -First 1; $r['host'] = $true }
    catch { $r['host'] = $false }
} catch { $r['module'] = $false; $r['error'] = $_.Exception.Message }
$r | ConvertTo-Json -Compress
"""
```

**인증 방식이 잘못되면 AUTHENTICATED 단계에서 걸러진다.** 실패 시 시도한 인증 방식을 `detail`에 포함하여
관리자가 조정할 수 있게 한다 (자격증명은 제외).

경로 B에서 `module=false`는 **인증이 아니라 대상 서버가 SCVMM이 아니라는 뜻**이다.
연결 유형을 잘못 골랐을 때 가장 흔한 실패이므로, 오류 문구를 인증 실패와 명확히 구분한다.

> **[검증 필요]**: `Get-SCUserRole`로 현재 계정의 역할을 조회할 수 있는지, 그리고
> `Read-Only Administrator` 역할 자체가 이 조회를 허용하는지. 허용되지 않으면 역할 표시는 포기하고
> `vm`/`host` 프로브 결과만 사용한다 (**역할 조회 실패를 연결 실패로 판정하지 않는다**).

---

## 11. 예외 변환 (`errors.py`)

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
    # 경로 B — 대상이 SCVMM 서버가 아니거나 콘솔이 없다. 연결 유형 선택 오류일 가능성이 높다
    if "virtualmachinemanager" in msg.lower():
        return ValidationError(
            "대상 서버에서 SCVMM 모듈을 찾을 수 없습니다. "
            "SCVMM 관리 서버 주소가 맞는지, 연결 유형이 올바른지 확인하세요.",
            field="kind",
        )
    if "cannot find the type" in msg.lower() or "is not recognized" in msg.lower():
        return CollectionError(
            "필요한 PowerShell 모듈이 없습니다 (Hyper-V / FailoverClusters / VirtualMachineManager)."
        )
    return CollectionError(f"Hyper-V 수집 오류: {sanitize_message(msg)}")
```

**모듈 부재를 `AuthenticationError`로 판정하지 않는다.** 재시도해도 소용없고, 관리자가 볼 조치가
"자격증명 확인"이 되어버려 원인에서 멀어진다. 설정 오류로 분류해 연결 유형 재확인을 유도한다.

**WinRM은 인증 실패를 여러 형태로 보고한다.** 문자열 매칭에 의존하는 부분이 있으므로
**모호하면 재시도하지 않는 쪽(`AuthenticationError`)으로 판정한다.** 계정 잠금이 재시도 지연보다 훨씬 큰 피해다.

> **[검증 필요]**: `pypsrp`가 실제로 던지는 인증 예외 타입을 확인하여 문자열 매칭을 타입 매칭으로 교체한다.

---

## 12. 구현 순서

**공통 → 경로 B → (필요할 때만) 경로 A 순으로 만든다.**
SCVMM 도입이 확정되었으므로 경로 B가 주 경로다 (D-012, `plans/ROADMAP.md` §20.1).
**경로 A는 SCVMM 미관리 호스트가 실사로 확인된 뒤에만 착수한다.**

| # | 작업 | 검증 |
|---|---|---|
| 0 | **`docs/00_research_notes.md` §11 미검증 항목 확인** (pypsrp 인증·비동기, §4.2·§7.2의 SCVMM 항목) | 결과를 조사 노트에 반영 |
| 0b | **SCVMM 계정 준비** — `Remote Management Users` + `Read-Only Administrator` (§4.3) | §10 권한 프로브가 전부 통과 |
| 1 | `errors.py` | 인증 실패 → `AuthenticationError(retryable=False)`, 모듈 부재 → `ValidationError` |
| 2 | `session.py` 인증 분기 | NTLM/Kerberos 연결, 실패 시 세션 정리 |
| 3 | `runner.py` + `parse_ps_json` | **단일 항목이 배열로 정규화**, 빈 출력, 깨진 JSON |
| 4 | `normalize.py` | 전원 상태 두 경로 매핑, MAC·IP 정규화 |
| **경로 B (주)** | | |
| 5 | `SCRIPT_SCVMM_LIST_VMS` | 실제 SCVMM에서 실행하여 출력 캡처 → fixture 커밋. **`VMId` 존재 여부 확인** |
| 6 | `scvmm_mapper.py` 게스트 정보 | IP·OS 부재 시 `TOOLS_NOT_INSTALLED`, OS 출처 `vm_config` (§8.5) |
| 7 | `scvmm_mapper.py` VM/디스크/어댑터 | `native_id`가 `VMId`, MAC 정규화, VHD 타입 |
| 8 | `scvmm_mapper.py` 호스트·클러스터·스토리지·네트워크 | 클러스터 소속이 호스트 경유로 채워짐 |
| 9 | `scvmm_reader.py` | **계약 테스트 스위트(계획 03 §9) 통과** |
| 10 | 규모 실측 | VM 총 건수와 1회 조회 소요 시간 → 분할 조회 필요 여부 판단 (§7.5) |
| **실사** | | |
| 11 | **SCVMM 미관리 호스트 조사** | 없으면 **여기서 종료**. 있으면 SCVMM 편입 가능 여부부터 검토 (§4.3.2) |
| **경로 A (조건부)** | | |
| 12 | **JEA 역할 기능·세션 구성 배포** (§4.3.1) | 비관리자 계정으로 접속 성공, 허용 함수 외 실행 차단 확인 |
| 13 | `SCRIPT_LIST_VMS` + JEA 함수 등가 확인 | 두 실행 경로가 **같은 JSON**을 반환 |
| 14 | KVP 파싱 | XML 항목에서 Name/Data 추출, 세미콜론 IP 분리 |
| 15 | `host_mapper.py` 게스트 정보 | 통합 서비스 미동작 VM 분기 |
| 16 | `host_mapper.py` VM/디스크/어댑터 | 정수 EnabledState 매핑, MAC 정규화, VHD 타입 |
| 17 | 호스트·스위치·스토리지 매핑 | 용량 바이트 통일 |
| 18 | 클러스터 노드 순회 | **노드 1개 실패 시 나머지 수집 계속** |
| 19 | `host_reader.py` | **경로 B와 동일한 계약 테스트 스위트 통과** |
| 20 | 두 경로 교차 확인 | 같은 VM을 양쪽으로 수집했을 때 **`native_id`가 일치** (§8.4) |

**11번을 형식적으로 넘기지 않는다.** 독립 호스트가 없으면 경로 A를 만들 이유가 없고,
있더라도 SCVMM에 편입할 수 있다면 JEA 구성보다 그쪽이 싸다.

**20번은 경로 A를 만든 경우의 최종 검증이다.** 두 경로가 같은 VM에 같은 식별자를 주지 못하면
호스트를 SCVMM에 편입하는 시점에 인벤토리가 통째로 중복된다.

## 13. 완료 기준

**공통**

- [ ] `arch_check.py` 통과 — vcenter 미참조, 읽기 전용 메서드만
- [ ] 계약 테스트 스위트 14종이 **두 경로 모두에서** 통과 (04와 동일 스위트)
- [ ] `parse_ps_json`이 단일 객체·배열·빈 출력을 모두 처리
- [ ] NTLM·Kerberos 인증 방식이 설정으로 선택되고 동작
- [ ] 링크로컬·루프백 IP가 결과에 없음
- [ ] WinRM/WMI/VMM 예외가 어댑터 밖으로 나오지 않음
- [ ] 유스케이스 코드에 **경로 분기가 없음** — 팩토리(계획 03 §7)에서만 갈린다

**경로 B — SCVMM (주 경로)**

- [ ] WinRM 접속 계정이 SCVMM 서버의 **로컬 관리자가 아님** — `Remote Management Users` (§4.3)
- [ ] VMM 역할이 **`Read-Only Administrator`**이며, 그 역할로 §10 권한 프로브가 전부 통과
- [ ] `native_id`가 **`VMId`(Hyper-V VM GUID)**이며 VMM 객체 ID가 아님 (§8.4)
- [ ] `VMId`가 없는 VM이 **수집에서 제외**되고 건수가 `error`에 기록됨
- [ ] 게스트 OS가 `(구성값)`으로 표시됨 — `os_source == vm_config` (§8.5)
- [ ] 대상이 SCVMM이 아닐 때 **인증 실패가 아닌 설정 오류**로 판정 (§11)
- [ ] **상태 변경·DB 갱신 cmdlet 부재** — 특히 `Read-SCVirtualMachine`(새로 고침은 쓰기다)
- [ ] SCVMM 고유 개념(Cloud·호스트 그룹·논리 네트워크)이 도메인 모델에 침투하지 않음

**경로 A — Hyper-V 관리자 (구현한 경우에만, §12-11 실사 통과 후)**

- [ ] **JEA 제약 세션으로 접속**하며, 포탈 계정이 `Hyper-V Administrators`가 **아님** (§4.3.1)
- [ ] JEA 세션에서 **허용 함수 외 cmdlet 실행이 차단**됨을 실측으로 확인
- [ ] JEA 경로와 직접 스크립트 경로가 **같은 JSON**을 반환 (§12-13)
- [ ] KVP 미제공 VM이 `TOOLS_NOT_INSTALLED`/`TOOLS_NOT_RUNNING`으로 매핑
- [ ] KVP의 세미콜론 구분 IP가 분리·정규화됨
- [ ] 전원 상태가 정수 `EnabledState` 기준으로 매핑 (로케일 무관)
- [ ] 클러스터 연결에서 노드 순회, 일부 실패 시 부분 성공 + error 기록
- [ ] **상태 변경 cmdlet 부재**: `Set-VM`, `Start-VM`, `Stop-VM`, `Remove-VM`, `Checkpoint-VM`, `Restore-VMSnapshot`, `New-VM`, `Rename-VM`, `Move-VM`

## 14. 주의사항

- **상태 변경 cmdlet 금지.** PowerShell은 문자열이라 arch-check가 잡지 못한다.
  `host_scripts.py`·`scvmm_scripts.py`를 verifier가 직접 검토하고, 다음으로 확인한다:
  ```bash
  grep -nE "(Set|Start|Stop|Remove|New|Rename|Move|Restore|Checkpoint|Export|Import)-VM" src/infrastructure/hyperv/
  grep -nE "(Set|New|Remove|Start|Stop|Save|Suspend|Resume|Repair|Move|Register|Unregister|Read|Install|Reset|Restore|Update|Grant|Revoke)-SC" src/infrastructure/hyperv/
  ```
  `Get-VM`·`Get-VMSnapshot`·`Get-SC*` 등 조회 cmdlet만 존재해야 한다.
- **`Read-SC*`는 조회가 아니다.** `Read-SCVirtualMachine`은 VMM DB를 갱신하는 쓰기 동작이다.
  이름이 `Read-`로 시작해 검사에서 빠지기 쉬우므로 위 grep 패턴에 명시적으로 포함했다 (§8.5).
- **원격 PowerShell은 느리다.** 경로 A는 호스트당 호출을 최소화하고, `Get-VHD`처럼 VM 수만큼 반복되는
  호출은 성능을 재확인한다. 경로 B는 `Get-SCVMCheckpoint`의 VM별 호출이 같은 문제를 만든다 (§7.2).
- `ConvertTo-Json`의 단일 항목 문제(§5.1)와 `-Depth` 부족을 주의한다.
- 날짜는 스크립트에서 `.ToString('o')`로 ISO 8601 문자열화한다. `/Date(...)/` 파싱을 피한다.
- 클러스터 이름 접속은 임의 노드로 라우팅된다. **노드별 개별 접속**이 필요하다 (§6.3).
- **SCVMM과 호스트를 동시에 등록하지 않는다** (§2.1). 같은 VM이 두 자원으로 생성된다.
- 개발·테스트는 목 커넥터로 한다 (CST-04). 목 커넥터는 **두 경로의 응답 형태를 모두** 재현해야 한다.
