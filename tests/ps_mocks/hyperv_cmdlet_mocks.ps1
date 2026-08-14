# Hyper-V / SCVMM cmdlet 목 (계획 05 §14 — "목 커넥터는 두 경로의 응답 형태를 모두 재현").
#
# 실제 Windows PowerShell 5.1에서 src/infrastructure/hyperv/*_scripts.py 의 스크립트
# 원본을 실행할 수 있도록, 스크립트가 호출하는 cmdlet을 함수로 섀도잉한다
# (PowerShell은 함수를 cmdlet보다 먼저 해석한다).
#
# 이 파일이 검증하는 것: 스크립트 구문·파이프라인·KVP XML 파싱·ConvertTo-Json 실제 출력.
# 검증하지 못하는 것: WinRM 전송·인증, 실제 Hyper-V/SCVMM 객체 모델 (실환경 실측 필요 — §11).
#
# 시나리오 데이터:
#   - mock-vm-kvp   : KVP 제공 (OS·FQDN·세미콜론 IPv4·링크로컬 포함) + BIOSGUID
#   - mock-vm-nokvp : KVP 없음 (통합 서비스 미동작) + BIOSGUID 없음
#   - mock-scvm-01  : 정상 SCVMM VM
#   - mock-scvm-stored : VMId 없음 (등록만 되고 미배포 — 수집 제외 대상)
#   WZONE_MOCK_SINGLE=1 이면 호스트 VM을 1대만 반환 → ConvertTo-Json 단일 객체 경로 검증

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$script:VmA = [guid]'a1b2c3d4-0000-1111-2222-333344445555'
$script:VmB = [guid]'b2c3d4e5-1111-2222-3333-444455556666'

function script:New-KvpXml {
    param([string]$Name, [string]$Data)
    # 실제 Msvm_KvpExchangeComponent.GuestIntrinsicExchangeItems 항목과 동일한 CIM-XML 형식
    '<INSTANCE CLASSNAME="Msvm_KvpExchangeDataItem">' +
    "<PROPERTY NAME=`"Name`" TYPE=`"string`"><VALUE>$Name</VALUE></PROPERTY>" +
    "<PROPERTY NAME=`"Data`" TYPE=`"string`"><VALUE>$Data</VALUE></PROPERTY>" +
    '</INSTANCE>'
}

$script:KvpItems = @{
    "$($script:VmA)" = @(
        (New-KvpXml 'OSName' 'Windows Server 2022 Standard')
        (New-KvpXml 'OSVersion' '10.0.20348')
        (New-KvpXml 'FullyQualifiedDomainName' 'mock-vm-kvp.example.invalid')
        (New-KvpXml 'NetworkAddressIPv4' '10.10.0.5;169.254.10.5')
        (New-KvpXml 'NetworkAddressIPv6' 'fe80::1')
    )
}

$script:BiosGuids = @{
    "$($script:VmA)" = '{4C4C4544-004D-3510-8054-B4C04F435831}'
}

# ── Hyper-V 모듈 (경로 A) ────────────────────────────────────

function Get-VM {
    [CmdletBinding()] param()
    $ids = if ($env:WZONE_MOCK_SINGLE -eq '1') { @($script:VmA) } else { @($script:VmA, $script:VmB) }
    foreach ($id in $ids) {
        if ($id -eq $script:VmA) {
            [PSCustomObject]@{
                Id = $id; Name = 'mock-vm-kvp'; State = 2; Generation = 2; Version = '10.0'
                ProcessorCount = 4; MemoryAssigned = 8589934592; MemoryStartup = 4294967296
                ComputerName = 'MOCK-HOST01'
            }
        } else {
            [PSCustomObject]@{
                Id = $id; Name = 'mock-vm-nokvp'; State = 3; Generation = 1; Version = '9.0'
                ProcessorCount = 2; MemoryAssigned = 0; MemoryStartup = 2147483648
                ComputerName = 'MOCK-HOST01'
            }
        }
    }
}

function Get-VMNetworkAdapter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$VM)
    process {
        if ($VM.Id -eq $script:VmA) {
            [PSCustomObject]@{ IPAddresses = @('10.10.0.5', 'fe80::1') }
        }
    }
}

function Get-CimInstance {
    [CmdletBinding()] param(
        [Parameter(Position = 0)][string]$ClassName,
        [string]$Namespace,
        [string]$Filter
    )
    switch ($ClassName) {
        'Msvm_ComputerSystem' {
            if ($Filter -and $Filter -match "'(.+)'") {
                [PSCustomObject]@{ MockVmId = $Matches[1] }
            } else {
                [PSCustomObject]@{ MockVmId = "$($script:VmA)" }   # 프로브의 무필터 조회
            }
        }
        'Msvm_VirtualSystemSettingData' {
            if ($Filter -match "'(.+)'" -and $script:BiosGuids.ContainsKey($Matches[1])) {
                [PSCustomObject]@{ BIOSGUID = $script:BiosGuids[$Matches[1]] }
            }
        }
        'Win32_OperatingSystem' {
            [PSCustomObject]@{ Caption = 'Microsoft Windows Server 2022 Datacenter (mock)' }
        }
        default { }
    }
}

function Get-CimAssociatedInstance {
    [CmdletBinding()] param($InputObject, [string]$ResultClassName)
    $items = $script:KvpItems["$($InputObject.MockVmId)"]
    if ($items) {
        [PSCustomObject]@{ GuestIntrinsicExchangeItems = $items }
    }
}

# ── FailoverClusters 모듈 (경로 A — 클러스터) ────────────────

# 스크립트의 `Import-Module ... -ErrorAction Stop`이 실제 모듈을 찾지 않게 섀도잉한다
function Import-Module { [CmdletBinding()] param([Parameter(Position = 0)]$Name) }

function Get-Cluster {
    [CmdletBinding()] param()
    [PSCustomObject]@{ Name = 'MOCK-HVC01' }
}

function Get-ClusterNode {
    [CmdletBinding()] param()
    [PSCustomObject]@{ Name = 'mock-n1.example.invalid'; State = 'Up'; Id = '1' }
    [PSCustomObject]@{ Name = 'mock-n2.example.invalid'; State = 'Down'; Id = '2' }
}

# ── VirtualMachineManager 모듈 (경로 B) ──────────────────────

function Get-SCVMMServer {
    [CmdletBinding()] param([string]$ComputerName)
    [PSCustomObject]@{ UserName = 'DOMAIN\svc-inventory'; ProductVersion = '10.22.1287.0' }
}

function Get-SCVirtualMachine {
    [CmdletBinding()] param($VMMServer, [switch]$All)
    [PSCustomObject]@{
        VMId = $script:VmA; Name = 'mock-scvm-01'; VirtualMachineState = 'Running'
        Generation = 2; Version = '10.0'; CPUCount = 4; Memory = 8192
        OperatingSystem = [PSCustomObject]@{ Name = 'Windows Server 2022 Standard' }
        VMHost = [PSCustomObject]@{ Name = 'mock-hv01.example.invalid' }
        VirtualNetworkAdapters = @(
            [PSCustomObject]@{ IPv4Addresses = @('10.10.0.5'); IPv6Addresses = @() }
        )
    }
    [PSCustomObject]@{
        VMId = $null; Name = 'mock-scvm-stored'; VirtualMachineState = 'Stored'
        Generation = 1; Version = $null; CPUCount = 1; Memory = 1024
        OperatingSystem = $null; VMHost = $null; VirtualNetworkAdapters = @()
    }
}

function Get-SCUserRole {
    [CmdletBinding()] param($VMMServer)
    [PSCustomObject]@{ Members = @('DOMAIN\svc-inventory'); Profile = 'ReadOnlyAdmin' }
}
