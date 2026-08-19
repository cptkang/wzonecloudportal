"""SCVMM(VirtualMachineManager) 수집 스크립트 — 경로 B (계획 05 §7).

SCVMM 서버 1회 접속으로 fabric 전체를 얻는다. **호스트 순회가 없다.**

Step 1 도메인 모델(MVP) 범위로 축소했다 — 계획 05 §7.2와의 대조:
- 유지: Id(=VMId), Name, State, Generation, Version, ProcessorCount, MemoryMB,
  OperatingSystem, HostName, Adapters(IP — 게스트 판정용 §8.5)
- 제외(Step 4): Disks, Checkpoints, DynamicMemory 상세, CreationTime/ModifiedTime,
  Owner(§8.6 — 포탈 메타데이터와 혼합 금지), Description
- 제외(Step 6): ClusterName, HostGroupPath, 호스트·클러스터·스토리지·네트워크 스크립트

**조회 cmdlet만 쓴다** (D-005). VMM의 `Read-` 접두사 cmdlet(VM 새로 고침)은 이름과 달리
VMM DB를 갱신하는 쓰기 동작이므로 금지다 (계획 05 §8.5·§14). 검사:
grep -nE "(Set|New|Remove|Start|Stop|Save|Suspend|Resume|Repair|Move|Register|Unregister|Read|Install|Reset|Restore|Update|Grant|Revoke)-SC" src/infrastructure/hyperv/
"""  # noqa: E501 - 문서용 grep 명령은 쪼개면 그대로 복사해 쓸 수 없다

from __future__ import annotations

#: 모든 SCVMM 스크립트의 공통 서두 (계획 05 §7.1).
#: `-ComputerName localhost` — 접속 대상이 SCVMM 서버 자신이므로 이중 홉(CredSSP)이
#: 생기지 않는다 (§4.2). `-VMMServer $vmm`을 각 cmdlet에 명시하여 기본 연결에 의존하지 않는다.
SCVMM_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
Import-Module VirtualMachineManager -ErrorAction Stop
$vmm = Get-SCVMMServer -ComputerName localhost
"""

#: VM 목록 (계획 05 §7.2의 MVP 축소판).
#: `Id`는 VMM 객체 ID가 아니라 **Hyper-V VM GUID(VMId)**다 — CI 식별의 핵심 (§8.4).
#: VMId가 없는 VM(VMM에 등록만 되고 배포되지 않음)은 $null로 내려보내고 리더가 제외한다.
SCRIPT_SCVMM_LIST_VMS = SCVMM_PREAMBLE + r"""
Get-SCVirtualMachine -VMMServer $vmm -All | ForEach-Object {
    $vm = $_
    [PSCustomObject]@{
        Id              = if ($vm.VMId) { $vm.VMId.ToString() } else { $null }
        Name            = $vm.Name
        State           = $vm.VirtualMachineState.ToString()
        Generation      = $vm.Generation
        Version         = $vm.Version
        ProcessorCount  = $vm.CPUCount
        MemoryMB        = $vm.Memory
        OperatingSystem = $vm.OperatingSystem.Name
        HostName        = $vm.VMHost.Name
        Adapters        = @($vm.VirtualNetworkAdapters | ForEach-Object {
            [PSCustomObject]@{
                IPv4 = @($_.IPv4Addresses)
                IPv6 = @($_.IPv6Addresses)
            }
        })
    }
} | ConvertTo-Json -Depth 4 -Compress
"""

#: 권한 프로브 (계획 05 §10) — 모듈 로드·서버 연결·역할·VM 조회를 한 번에 확인한다.
#: `module=false`는 인증이 아니라 **대상 서버가 SCVMM이 아니라는 뜻**이다.
#: 역할 조회 실패는 연결 실패로 판정하지 않는다 (§10 [검증 필요] — 실환경 확인 전 보수 동작).
SCRIPT_PROBE_SCVMM = r"""
$r = @{}
try {
    Import-Module VirtualMachineManager -ErrorAction Stop
    $vmm = Get-SCVMMServer -ComputerName localhost -ErrorAction Stop
    $r['module'] = $true
    $r['version'] = $vmm.ProductVersion.ToString()
    try {
        $r['role'] = (Get-SCUserRole -VMMServer $vmm |
                      Where-Object { $_.Members -contains $vmm.UserName } |
                      Select-Object -First 1 -ExpandProperty Profile) -as [string]
    } catch { $r['role'] = $null }
    try { $null = Get-SCVirtualMachine -VMMServer $vmm | Select-Object -First 1; $r['vm'] = $true }
    catch { $r['vm'] = $false }
} catch { $r['module'] = $false; $r['error'] = $_.Exception.Message }
$r | ConvertTo-Json -Compress
"""
