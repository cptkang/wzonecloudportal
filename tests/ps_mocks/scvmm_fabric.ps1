# SCVMM fabric 목업 — VirtualMachineManager 모듈 cmdlet 시뮬레이터
#
# 실제 SCVMM은 시뮬레이터도 컨테이너도 없다 (D-018). Windows Server + SQL Server +
# 도메인이 필요해 개발 PC에 세울 수 없다 — 근거와 조사 내용은 docs/06_scvmm_test_environment.md.
# 이 파일은 그 조사 결과를 근거로 만든 **목업 fabric**이며, 수집 스크립트 원본
# (src/infrastructure/hyperv/scvmm_scripts.py)을 실제 Windows PowerShell 5.1에서
# 실행할 수 있게 cmdlet을 함수로 섀도잉한다 (PowerShell은 함수를 cmdlet보다 먼저 해석한다).
#
# tests/ps_mocks/hyperv_cmdlet_mocks.ps1 과의 역할 구분:
#   - hyperv_cmdlet_mocks.ps1 : 경로 A(Hyper-V/WMI/KVP) + 경로 B 최소 2건. 기존 스크립트 회귀용
#   - 이 파일                 : 경로 B(SCVMM) 전용. 상태·경계·오류 시나리오를 넓게 재현
#
# 시나리오 전환: 환경변수 WZONE_SCVMM_SCENARIO
#   normal(기본) | single | empty | large | no_module | connect_fail | no_permission | no_role
#
# 검증하는 것 : 스크립트 구문·파이프라인·ConvertTo-Json 실물 출력·리더/매퍼 관통
# 검증 못 하는 것: WinRM 전송·인증, 실제 VMM 객체 모델 (연구 노트 §11-11~15 — 실환경 실측)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$script:Scenario = if ($env:WZONE_SCVMM_SCENARIO) { $env:WZONE_SCVMM_SCENARIO } else { 'normal' }

# ── 목 fabric 데이터 ─────────────────────────────────────────
#
# 속성명은 Microsoft Learn의 Get-SCVirtualMachine 문서와 계획 05 §7.2를 따른다.
# [확인됨] Name, HostName, OperatingSystem, CPUCount, Memory, VirtualMachineState, Owner, Description
# [미확인] VMId, Version, Generation, VirtualNetworkAdapters.IPv4Addresses
#          — 2차 자료 기반이며 실환경 확인 항목이다 (연구 노트 §11-11·13)
# 실제 객체는 VMHost·OperatingSystem이 문자열이 아니라 객체다. 목업도 객체로 만들어
# 스크립트의 `$vm.VMHost.Name` / `$vm.OperatingSystem.Name` 접근을 그대로 재현한다.

# $Name에 [string] 타입 제약을 두지 않는다 — PowerShell이 $null을 ''로 캐스팅해
# "OS 미지정"이 빈 문자열로 새어 나간다. 실제 VMM은 객체 자체가 $null이고,
# `$vm.OperatingSystem.Name`은 $null을 반환한다.
function script:New-MockOs {
    param($Name)
    if ($null -eq $Name) { return $null }
    [PSCustomObject]@{ Name = $Name }
}

function script:New-MockHost {
    param($Name)
    if ($null -eq $Name) { return $null }
    [PSCustomObject]@{ Name = $Name }
}

function script:New-MockAdapter {
    param([string[]]$IPv4 = @(), [string[]]$IPv6 = @())
    [PSCustomObject]@{ IPv4Addresses = $IPv4; IPv6Addresses = $IPv6 }
}

function script:New-MockVm {
    param(
        $VMId, [string]$Name, [string]$State, $Generation, $Version,
        $CPUCount, $Memory, $OsName, $HostName, $Adapters = @()
    )
    [PSCustomObject]@{
        VMId                   = $VMId
        Name                   = $Name
        VirtualMachineState    = $State
        Generation             = $Generation
        Version                = $Version
        CPUCount               = $CPUCount
        Memory                 = $Memory          # SCVMM은 MB 단위 (경로 A의 바이트와 다름)
        OperatingSystem        = (New-MockOs $OsName)
        VMHost                 = (New-MockHost $HostName)
        HostName               = $HostName         # 실제 VM 객체도 이 속성을 직접 노출한다
        VirtualNetworkAdapters = @($Adapters)
        # 수집하지 않는 속성 — 존재해도 스크립트가 읽지 않음을 확인하기 위해 둔다 (§8.6)
        Owner                  = 'CONTOSO\vm-owner'
        Description            = '목업 VM — 포탈이 읽지 않아야 하는 필드'
    }
}

function script:Get-MockFabric {
    switch ($script:Scenario) {
        'empty' { return @() }

        'single' {
            return @(
                (New-MockVm ([guid]'a1b2c3d4-0000-1111-2222-333344445555') 'mock-scvm-running' `
                    'Running' 2 '10.0' 4 8192 'Windows Server 2022 Standard' 'mock-hv01.example.invalid' `
                    @(New-MockAdapter -IPv4 @('10.10.0.5') -IPv6 @()))
            )
        }

        'large' {
            # JSON 크기·파싱·수집 시간 경로 확인용. VMId는 결정적으로 생성한다.
            # 마지막 그룹은 정확히 12자리여야 한다 — 8자 + D4 4자 (짧으면 [guid] 캐스팅이 실패한다)
            return @(1..500 | ForEach-Object {
                $n = '{0:D4}' -f $_
                New-MockVm ([guid]("00000000-0000-4000-8000-00000000$n")) "mock-scvm-$n" `
                    'Running' 2 '10.0' 2 4096 'Windows Server 2022 Standard' `
                    ('mock-hv{0:D2}.example.invalid' -f (($_ % 8) + 1)) `
                    @(New-MockAdapter -IPv4 @("10.20.$([math]::Floor($_ / 254)).$(($_ % 254) + 1)"))
            })
        }

        default {
            return @(
                # 1) 정상 — 링크로컬·IPv6 포함 (매퍼가 걸러내야 한다)
                (New-MockVm ([guid]'a1b2c3d4-0000-1111-2222-333344445555') 'mock-scvm-running' `
                    'Running' 2 '10.0' 4 8192 'Windows Server 2022 Standard' 'mock-hv01.example.invalid' `
                    @(New-MockAdapter -IPv4 @('10.10.0.5', '169.254.10.5') -IPv6 @('fe80::1'))),

                # 2) 전원 꺼짐 — Gen1(BIOS), IP 없음. OS 값은 VMM DB에 남아 있다
                (New-MockVm ([guid]'b2c3d4e5-1111-2222-3333-444455556666') 'mock-scvm-off' `
                    'PowerOff' 1 '9.0' 2 4096 'CentOS Linux 7 (64 bit)' 'mock-hv01.example.invalid' `
                    @(New-MockAdapter)),

                # 3) 일시 정지 — SUSPENDED로 매핑되어야 한다
                (New-MockVm ([guid]'c3d4e5f6-2222-3333-4444-555566667777') 'mock-scvm-paused' `
                    'Paused' 2 '10.0' 8 16384 'Windows Server 2019 Standard' 'mock-hv02.example.invalid' `
                    @(New-MockAdapter -IPv4 @('10.10.0.7'))),

                # 4) 저장됨 + OS/IP 모두 없음 → GuestInfo가 TOOLS_NOT_INSTALLED여야 한다 (FR-501)
                (New-MockVm ([guid]'d4e5f6a7-3333-4444-5555-666677778888') 'mock-scvm-saved' `
                    'Saved' 2 $null 2 2048 $null 'mock-hv02.example.invalid' @()),

                # 5) 라이브러리 저장 VM — VMId 없음 → 리더가 제외해야 한다 (§8.4)
                (New-MockVm $null 'mock-scvm-stored' `
                    'Stored' 1 $null 1 1024 $null $null @()),

                # 6) 미매핑 상태 — UNKNOWN으로 떨어지고 원본 값이 로그에 남아야 한다 (§8.1)
                (New-MockVm ([guid]'e5f6a7b8-4444-5555-6666-777788889999') 'mock-scvm-deploying' `
                    'Deploying' 2 '10.0' 4 8192 'Windows Server 2022 Standard' 'mock-hv03.example.invalid' `
                    @(New-MockAdapter))
            )
        }
    }
}

# ── VirtualMachineManager 모듈 cmdlet (조회) ─────────────────

# 스크립트의 `Import-Module VirtualMachineManager -ErrorAction Stop`을 가로챈다.
# no_module 시나리오는 대상 서버가 SCVMM이 아닌 경우를 재현한다 (리더가 ValidationError로 변환).
function Import-Module {
    [CmdletBinding()] param([Parameter(Position = 0)]$Name)
    if ($script:Scenario -eq 'no_module' -and "$Name" -eq 'VirtualMachineManager') {
        throw "The specified module 'VirtualMachineManager' was not loaded because no valid module file was found in any module directory."
    }
}

function Get-SCVMMServer {
    [CmdletBinding()]
    param([Parameter(Position = 0)][string]$ComputerName, [string]$ConnectAs)
    if ($script:Scenario -eq 'connect_fail') {
        throw "VMMServer connection to $ComputerName failed. (Error ID: 1601)"
    }
    [PSCustomObject]@{
        Name           = $ComputerName
        UserName       = 'CONTOSO\svc-inventory'
        ProductVersion = '10.22.1287.0'    # [미확인] 속성명 — 실환경 확인 항목
        Port           = 8100              # VMM 기본 포트 (문서 확인)
    }
}

function Get-SCVirtualMachine {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)][string]$Name,
        $VMMServer,
        [switch]$All,
        $VMHost,
        $ID
    )
    if ($script:Scenario -eq 'no_permission') {
        throw "You do not have permission to perform this action. (Error ID: 1604)"
    }
    $vms = Get-MockFabric
    if ($Name) { $vms = @($vms | Where-Object { $_.Name -eq $Name }) }
    if ($ID)   { $vms = @($vms | Where-Object { "$($_.VMId)" -eq "$ID" }) }
    # 배열을 그대로 반환하면 호출부의 ForEach-Object 파이프라인이 실제와 같이 동작한다
    foreach ($vm in $vms) { $vm }
}

function Get-SCUserRole {
    [CmdletBinding()] param($VMMServer, [string]$Name, [string]$UserRoleProfile)
    if ($script:Scenario -eq 'no_role') { throw "Access denied to user role objects. (Error ID: 1604)" }
    $roles = @(
        [PSCustomObject]@{ Name = 'Read-Only Inventory'; Members = @('CONTOSO\svc-inventory'); Profile = 'ReadOnlyAdmin' }
        [PSCustomObject]@{ Name = 'Administrator';       Members = @('CONTOSO\admin');         Profile = 'Administrator' }
    )
    if ($UserRoleProfile) { $roles = @($roles | Where-Object { $_.Profile -eq $UserRoleProfile }) }
    foreach ($r in $roles) { $r }
}

# ── 쓰기 cmdlet 트랩 (D-005 / 계획 05 §14) ────────────────────
#
# 수집 스크립트가 자원 변경 cmdlet을 호출하면 **즉시 실패**한다.
# grep 검사(§14)는 소스 문자열만 보지만 이 트랩은 실행 경로에서 잡는다.
# VMM의 `Read-SCVirtualMachine`은 이름과 달리 VMM DB를 갱신하는 쓰기 동작이므로 여기 포함된다.

function script:Deny-Write {
    param([string]$Cmdlet)
    throw "읽기 전용 위반: 수집 경로가 쓰기 cmdlet '$Cmdlet'을 호출했습니다 (D-005, 계획 05 §14)."
}

function Set-SCVirtualMachine       { Deny-Write 'Set-SCVirtualMachine' }
function New-SCVirtualMachine       { Deny-Write 'New-SCVirtualMachine' }
function Remove-SCVirtualMachine    { Deny-Write 'Remove-SCVirtualMachine' }
function Read-SCVirtualMachine      { Deny-Write 'Read-SCVirtualMachine' }
function Start-SCVirtualMachine     { Deny-Write 'Start-SCVirtualMachine' }
function Stop-SCVirtualMachine      { Deny-Write 'Stop-SCVirtualMachine' }
function Save-SCVirtualMachine      { Deny-Write 'Save-SCVirtualMachine' }
function Suspend-SCVirtualMachine   { Deny-Write 'Suspend-SCVirtualMachine' }
function Resume-SCVirtualMachine    { Deny-Write 'Resume-SCVirtualMachine' }
function Repair-SCVirtualMachine    { Deny-Write 'Repair-SCVirtualMachine' }
function Reset-SCVirtualMachine     { Deny-Write 'Reset-SCVirtualMachine' }
function Move-SCVirtualMachine      { Deny-Write 'Move-SCVirtualMachine' }
function Restore-SCVirtualMachine   { Deny-Write 'Restore-SCVirtualMachine' }
function Set-SCVMHost               { Deny-Write 'Set-SCVMHost' }
function Register-SCVirtualMachine  { Deny-Write 'Register-SCVirtualMachine' }
function Unregister-SCVirtualMachine{ Deny-Write 'Unregister-SCVirtualMachine' }
function Grant-SCResource           { Deny-Write 'Grant-SCResource' }
function Revoke-SCResource          { Deny-Write 'Revoke-SCResource' }
