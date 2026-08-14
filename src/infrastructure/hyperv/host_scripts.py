"""Hyper-V 관리자 계열 수집 스크립트 — 경로 A (계획 05 §6·§10).

`Hyper-V`·`FailoverClusters` 모듈을 쓴다. **호출 횟수를 줄이기 위해 자원 유형별로
한 번씩만 호출한다** (원격 PowerShell은 느리다 — §2).

Step 1 도메인 모델(MVP) 범위로 축소했다 — 계획 05 §6.1과의 대조:
- 유지: Id, Name, State(정수), Generation, Version, ProcessorCount,
  MemoryAssigned/MemoryStartup(바이트), BiosGuid, KVP(게스트 OS·FQDN·IP — §8.2),
  Adapters(IP — 게스트 판정용), ComputerName
- 제외(Step 4): Disks(Get-VHD), Checkpoints(Get-VMSnapshot), Adapters의 MAC·스위치,
  DynamicMemory 상세, Uptime, CreationTime, Notes
- 제외(Step 6): SCRIPT_HOST_INFO(§6.2), SCRIPT_STORAGE·SCRIPT_SWITCHES(§6.4)

**JEA 환경에서는 이 스크립트를 세션으로 보낼 수 없다** (§4.3.1 — 제약 세션의 언어 모드).
역할 기능 파일(scripts/jea/)에 같은 본문이 함수로 배포되며, 어댑터는 함수 이름만 호출한다.
스크립트를 고치면 `python scripts/generate_jea_role.py`로 역할 파일을 재생성해 배포한다.

**조회 cmdlet만 쓴다** (D-005). 검사:
grep -nE "(Set|Start|Stop|Remove|New|Rename|Move|Restore|Checkpoint|Export|Import)-VM" src/infrastructure/hyperv/
"""

from __future__ import annotations

#: JEA 역할 기능 파일이 노출하는 함수 이름 (계획 05 §4.3.1)
FUNCTION_LIST_VMS = "Get-WzoneVmInventory"
FUNCTION_CLUSTER_NODES = "Get-WzoneClusterNodes"
FUNCTION_PROBE = "Get-WzoneProbe"

#: VM 기본 정보 + KVP 게스트 정보 (계획 05 §6.1의 MVP 축소판).
#: KVP(Msvm_KvpExchangeComponent)는 WMI로만 접근 가능하며 게스트 OS·IP의 유일한 경로다.
#: 항목은 XML 문자열이라 Name/Data 속성을 파싱해야 한다.
SCRIPT_LIST_VMS = r"""
$ErrorActionPreference = 'Stop'
Get-VM | ForEach-Object {
    $vm = $_
    $kvp = @{}
    try {
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
        Id             = $vm.Id.ToString()
        Name           = $vm.Name
        State          = [int]$vm.State
        Generation     = $vm.Generation
        Version        = $vm.Version
        ProcessorCount = $vm.ProcessorCount
        MemoryAssigned = $vm.MemoryAssigned
        MemoryStartup  = $vm.MemoryStartup
        ComputerName   = $vm.ComputerName
        BiosGuid       = $bios
        IntegrationOk  = [bool]$kvp.Count
        KvpOSName      = $kvp['OSName']
        KvpOSVersion   = $kvp['OSVersion']
        KvpFQDN        = $kvp['FullyQualifiedDomainName']
        KvpIPv4        = $kvp['NetworkAddressIPv4']
        KvpIPv6        = $kvp['NetworkAddressIPv6']
        Adapters       = @($vm | Get-VMNetworkAdapter | ForEach-Object {
            [PSCustomObject]@{ IPAddresses = @($_.IPAddresses) }
        })
    }
} | ConvertTo-Json -Depth 4 -Compress
"""

#: 클러스터 노드 목록 (계획 05 §6.3). 클러스터 이름 접속은 임의 노드로 라우팅되므로,
#: 이 목록을 얻은 뒤 **노드별로 개별 접속**해야 모든 VM을 수집할 수 있다.
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

#: 권한 프로브 (계획 05 §10) — Hyper-V 모듈과 WMI(KVP) 접근을 각각 확인한다.
#: §4.3의 권한 문제(모듈은 되는데 WMI가 안 되는 계정)를 연결 테스트에서 드러낸다.
SCRIPT_PROBE_PERMISSIONS = r"""
$r = @{}
try { $null = Get-VM | Select-Object -First 1; $r['vm'] = $true } catch { $r['vm'] = $false }
try { $null = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem `
             -ErrorAction Stop | Select-Object -First 1; $r['wmi'] = $true } catch { $r['wmi'] = $false }
try { $r['os'] = (Get-CimInstance Win32_OperatingSystem).Caption } catch { $r['os'] = $null }
$r | ConvertTo-Json -Compress
"""
