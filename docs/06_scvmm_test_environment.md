# SCVMM API 조사와 목업 테스트 환경

> 작성일: 2026-08-15
> 관련: D-012(Hyper-V 2경로), D-018(어댑터 조기 구현), `plans/05-hyperv-adapter.md`,
> `docs/00_research_notes.md` §11-11~15·17·18

## 1. 요약

| 질문 | 답 |
|---|---|
| SCVMM에 REST API나 SDK가 있는가 | **온프레미스 SCVMM에는 없다.** PowerShell 모듈이 유일한 공식 자동화 경로다 (§2) |
| 개발 PC에 실제 SCVMM을 세울 수 있는가 | **불가능하다.** Windows Server + SQL Server + 도메인이 필요하다 (§3) |
| 그럼 무엇으로 검증하는가 | **목업 fabric**을 만들어 수집 스크립트 원본을 실제 PowerShell로 실행하고 리더까지 관통시킨다 (§4~§6) |
| 목업으로 안 되는 것은 | WinRM 전송·인증·세션 수명, 실제 VMM 객체 모델. 실환경 실측 항목으로 남는다 (§7) |

## 2. SCVMM 자동화 인터페이스 조사

### 2.1 온프레미스 SCVMM — PowerShell 모듈이 유일하다

Microsoft의 공식 답변은 명확하다: **"SCVMM doesn't provide API services. However, SCVMM
provides the cmdlets, which are delivered in a Windows PowerShell module."**
VMM에서 할 수 있는 모든 작업은 cmdlet으로 수행한다.

| 인터페이스 | 온프레미스 SCVMM | 이 프로젝트에서 |
|---|---|---|
| **PowerShell 모듈 `virtualmachinemanager`** | **유일한 공식 경로** | **채택** (D-012 경로 B) |
| REST API | 없음 | — |
| .NET SDK (`Microsoft.SystemCenter.VirtualMachineManager.dll`) | cmdlet의 구현 어셈블리. C#에서 PowerShell 모듈을 호스팅하는 방식으로만 사용 | 미채택 — Python 프로젝트이며 이점이 없다 |
| Azure Arc-enabled SCVMM REST API | 존재하나 **Azure 연결이 전제** | **부적합** — 폐쇄망 환경 |

> **Azure Arc 경로를 선택지로 두지 않는다.** Arc-enabled SCVMM은 REST API·다국어 SDK를
> 제공하지만 VMM 서버를 Azure에 온보딩해야 한다. 이 포탈은 사내 폐쇄망 인벤토리 시스템이며,
> 인벤토리 정보를 외부로 내보내는 경로를 만드는 것 자체가 요건에 어긋난다 (NFR-206).

### 2.2 이 프로젝트가 쓰는 cmdlet

`src/infrastructure/hyperv/scvmm_scripts.py`가 호출하는 것 전부다. **모두 조회 cmdlet이다.**

| cmdlet | 용도 | 문서로 확인된 파라미터 |
|---|---|---|
| `Get-SCVMMServer` | VMM 서버 연결 | `-ComputerName`, `-ConnectAs`(DelegatedAdmin/ReadOnlyAdmin). 기본 포트 **TCP 8100** |
| `Get-SCVirtualMachine` | VM 목록 | `-Name`, `-VMMServer <ServerConnection>`, `-All`, `-VMHost`, `-ID <Guid>`, `-Cloud`, `-Service`, `-OnBehalfOfUser(Role)` |
| `Get-SCUserRole` | 역할 확인 | `-VMMServer`, `-Name`, `-UserRoleProfile`, `-ID` |

`Get-SCVirtualMachine`은 **`VirtualMachine` 객체**를 반환한다 (Microsoft Learn "Outputs").

> **`Read-SCVirtualMachine`은 조회 cmdlet이 아니다.** 이름과 달리 VMM DB를 갱신하는 쓰기
> 동작이다. 읽기 전용 원칙(D-005)에서 금지이며, §5의 목업이 실행 시점에 차단한다.

### 2.3 VM 객체 속성 — 확인된 것과 미확인

목업의 충실도는 이 구분에 달려 있다. **미확인 속성을 확인된 것처럼 다루지 않는다.**

| 속성 | 근거 | 상태 |
|---|---|---|
| `Name`, `Owner`, `Description`, `HostName`, `OperatingSystem`, `CPUCount`, `Memory` | Microsoft Learn `Get-SCVirtualMachine` 예제 2가 `Format-List`로 직접 나열 | **확인** |
| `Status`, `LibraryServer` | 동 문서 예제 3 | **확인** |
| `VirtualMachineState` | 동 문서 및 실무 예제. 값 `Running`·`PowerOff`·`Paused` 확인 | **부분 확인** — 전체 열거값 미확인 |
| `VMId` | 2차 자료가 Hyper-V VM GUID와 동일하다고 기술. **`native_id`의 근거** | **미확인** — 연구 노트 §11-11 |
| `Generation`, `Version` | 계획 05 §7.2의 가정 | **미확인** |
| `VirtualNetworkAdapters[].IPv4Addresses` | 계획 05 §7.2의 가정. 출처(KVP 중계 / VMM IP 풀)가 FR-501 판정을 가른다 | **미확인** — 연구 노트 §11-13 |
| `BiosGuid` | 문서에서 확인되지 않음 | **미확인** — 있으면 폴스타 매칭 규칙 1이 강해진다 (계획 14 §6.1.1) |

`Get-SCVMMServer`의 `ProductVersion`·`UserName`도 문서로 확인되지 않았다. 프로브 스크립트가
이 값을 쓰지만 **없어도 연결 실패로 판정하지 않도록** 되어 있다 (`scvmm_scripts.py` §10 주석).

## 3. 실제 테스트 환경 구성 — 판단과 근거

### 3.1 SCVMM 서버 요구사항 (Microsoft Learn, VMM 2025)

| 항목 | 요구사항 |
|---|---|
| OS | **Windows Server 2025** (Server Core 또는 Desktop Experience) |
| DB | **SQL Server 2019 또는 2022** (Standard/Enterprise). 설치 계정에 `sysadmin` 필요 |
| 선행 설치 | **Windows ADK** (+ WinPE 애드온), PowerShell 5.1, .NET 4.6 |
| 하드웨어 | 8코어 2GHz, RAM 최소 4GB(권장 16GB) |
| 도메인 | **Active Directory 도메인 필요** |

### 3.2 현재 개발 PC 실측

```
VirtualMachineManager 모듈 : 없음
Hyper-V / FailoverClusters : 없음
도메인 가입                : False
OS                         : Windows 11 Enterprise LTSC
메모리                     : 63.9 GB
디스크 여유                : E: 1.2 TB
```

하드웨어는 충분하지만 **소프트웨어 전제가 하나도 충족되지 않는다.** 실제 SCVMM을 세우려면
최소 Windows Server VM 2대(도메인 컨트롤러 + VMM 서버) + SQL Server + Hyper-V 호스트가 필요하고,
Hyper-V 기능 활성화에는 관리자 권한이, 미디어 확보에는 평가판 ISO 다운로드가 필요하다.

### 3.3 "VMM 콘솔만 설치" 경로도 성립하지 않는다

VMM 콘솔을 설치하면 `virtualmachinemanager` 모듈이 함께 들어온다. 콘솔은 로컬 관리자 권한만
있으면 설치할 수 있고 서버 버전과 일치해야 한다. 그러나:

- 설치 미디어(SCVMM 평가판 ISO)가 필요하다
- 모듈이 있어도 **`Get-SCVMMServer -ComputerName localhost`가 실제 VMM 서버(TCP 8100)를
  찾지 못해 실패한다.** cmdlet 응답을 얻을 수 없으므로 검증 가치가 없다

### 3.4 결론

**실제 SCVMM 테스트 환경은 이 단계에서 구성하지 않는다.** 근거는 위 3가지이며, 이는
D-018이 "실환경 검증은 되지 않았다"고 명시한 상태와 일치한다.

대신 **조사 결과를 근거로 목업 환경을 구성한다** (§4). 실환경 검증은 조직의 실제 SCVMM
서버에 읽기 전용 계정이 준비된 뒤 Step 2 실측에서 수행한다 (ROADMAP §15).

### 3.5 그래도 실제 환경을 만든다면 (참고 절차)

조직이 검증용 SCVMM 랩을 원한다면 순서는 다음과 같다. **이 작업에는 관리자 권한, 평가판
미디어, 반나절 이상의 시간이 든다.**

```
1. Hyper-V 기능 활성화 (관리자 권한, 재부팅)
     Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
2. VM1: Windows Server 2025 → AD DS 승격 (도메인 예: lab.invalid)
3. VM2: Windows Server 2025 → 도메인 가입 → SQL Server 2022 + Windows ADK 설치
4. VM2에 SCVMM 2025 설치 (평가판 180일)
5. VM3(또는 호스트 자체)을 Hyper-V 호스트로 SCVMM에 추가
6. 읽기 전용 계정 생성: Read-Only Administrator 역할 + Remote Management Users (D-012)
7. 포탈에서 scvmm 연결 등록 → 연결 테스트 → 수집
```

권장 자원: VM당 4vCPU/8GB, 총 디스크 200GB 이상. 라이선스는 평가판 정책을 조직이 확인해야 한다.

## 4. 목업 환경 구성

### 4.1 3계층 구조

```
tests/ps_mocks/scvmm_fabric.ps1          ← ① 목 fabric (cmdlet 섀도잉 + 시나리오 데이터)
        ↑ 선로드
src/infrastructure/hyperv/scvmm_scripts.py  수집 스크립트 **원본** (수정 없음)
        ↓ 실제 Windows PowerShell 5.1 실행
tests/fakes/local_ps_runner.py           ← ② PowerShellRunner 호환 러너 (WinRM 대신 로컬 실행)
        ↓ 주입
src/infrastructure/hyperv/scvmm_reader.py   ScvmmInventoryReader (프로덕션 코드, 수정 없음)
        ↓
tests/integration/test_scvmm_mock_fabric.py ← ③ 관통 테스트 22건
scripts/mock_scvmm.py                    ← 개발자용 실행기 (눈으로 확인)
```

**프로덕션 코드는 한 줄도 바꾸지 않았다.** 테스트가 `reader._runner`를 목 러너로 교체한다.
`LocalPowerShellRunner`는 `invoke_json` 시그니처만 맞추면 되므로 덕 타이핑으로 성립한다.

### 4.2 목 fabric이 재현하는 것

PowerShell은 **함수를 cmdlet보다 먼저 해석**한다. 이 성질을 이용해 `Get-SCVirtualMachine` 등을
함수로 정의하면 수집 스크립트 원본이 수정 없이 실행된다.

객체 형태를 실제와 맞췄다 — `OperatingSystem`과 `VMHost`는 문자열이 아니라 **객체**이므로
스크립트의 `$vm.OperatingSystem.Name` 접근이 그대로 재현된다.

> **`[string]` 타입 제약을 파라미터에 걸지 않는다.** PowerShell이 `$null`을 `''`로 캐스팅해
> "OS 미지정"이 빈 문자열로 새어 나간다. 구현 중 실제로 이 결함이 나왔고, 제약을 없애
> `OperatingSystem: null`이 나오도록 고쳤다. 실제 VMM은 객체 자체가 `$null`이다.

### 4.3 시나리오

`WZONE_SCVMM_SCENARIO` 환경변수로 전환한다.

| 시나리오 | 내용 | 확인하는 것 |
|---|---|---|
| `normal` (기본) | VM 6대 | 상태 5종, 링크로컬 필터, 게스트 3분법, VMId 없는 VM 제외 |
| `single` | 1대 | PowerShell이 배열 아닌 **단일 객체**를 낼 때의 정규화 (§5.1) |
| `empty` | 0대 | "VM 없음"과 "수집 실패"의 구분 |
| `large` | 500대 | JSON 크기·파싱·스트리밍, 식별자 충돌 없음 |
| `no_module` | `Import-Module` 실패 | SCVMM 아닌 서버 등록 → `ValidationError(field="kind")` |
| `connect_fail` | `Get-SCVMMServer` 실패 | 연결 실패의 도메인 예외 변환 |
| `no_permission` | VM 조회 거부 | **부분 실패가 예외가 아니라 outcome으로 보고**되는가 (FR-204) |
| `no_role` | 역할 조회 실패 | 역할을 못 읽어도 연결 실패로 판정하지 않는가 |

`normal`의 VM 6대는 각각 다른 경계를 노린다.

| VM | 상태 | 노리는 경계 |
|---|---|---|
| `mock-scvm-running` | Running | 링크로컬 `169.254`/`fe80` 제거 |
| `mock-scvm-off` | PowerOff | Gen1(BIOS), IP 없지만 OS는 있음 → `AVAILABLE` |
| `mock-scvm-paused` | Paused | `SUSPENDED` 매핑 |
| `mock-scvm-saved` | Saved | OS·IP 모두 없음 → **`TOOLS_NOT_INSTALLED`** (FR-501) |
| `mock-scvm-stored` | Stored | **VMId 없음 → 리더가 제외** (§8.4) |
| `mock-scvm-deploying` | Deploying | 미매핑 상태 → 추정하지 않고 `UNKNOWN` (§8.1) |

### 4.4 읽기 전용 트랩

목 fabric은 `Set-SC*`·`Remove-SC*`·`Start-SC*`·`Read-SCVirtualMachine` 등 **쓰기 cmdlet 18개를
정의해 호출 즉시 예외를 던진다.**

계획 05 §14의 grep 검사는 소스 문자열만 본다. 이 트랩은 **실행 경로에서** 잡으므로,
수집이 성공했다는 사실 자체가 쓰기 cmdlet을 부르지 않았다는 증거가 된다.
트랩 자체가 동작하는지도 별도 테스트로 확인한다 (`test_write_cmdlet_trap_actually_fires`).

## 5. 사용법

### 5.1 테스트

```bash
python -m pytest tests/integration/test_scvmm_mock_fabric.py -q   # 22건
python -m pytest tests/integration/test_ps_scripts_live.py -q     # 기존 스크립트 회귀
```

Windows가 아니면 전체 skip된다. 통합 테스트라 `conftest.py`가 PostgreSQL을 요구한다
(CLAUDE.md의 개발 환경 컨테이너).

### 5.2 눈으로 확인

```bash
python scripts/mock_scvmm.py                       # normal 시나리오 요약표
python scripts/mock_scvmm.py --scenario large --limit 5
python scripts/mock_scvmm.py --scenario no_module --probe
python scripts/mock_scvmm.py --raw                 # 원시 JSON 포함
```

출력 예:

```
NAME                    STATE        vCPU  MEM(MB)  GUEST                 HOST
mock-scvm-running       on              4     8192  available 10.10.0.5   mock-hv01.example.invalid
mock-scvm-saved         suspended       2     2048  tools_not_installed   mock-hv02.example.invalid
mock-scvm-stored        (제외: VMId 없음)

수집 5건 / 제외 1건 (원본 6행)
```

`scvmm_scripts.py`나 `scvmm_mapper.py`를 고친 뒤 결과가 어떻게 달라지는지 바로 볼 수 있다.

### 5.3 목 fabric에 시나리오를 추가할 때

1. `scvmm_fabric.ps1`의 `Get-MockFabric` switch에 분기를 추가한다
2. `local_ps_runner.py`의 `SCENARIOS` 튜플에 이름을 넣는다 (넣지 않으면 러너가 거부한다)
3. `test_scvmm_mock_fabric.py`에 그 시나리오가 무엇을 보장하는지 테스트를 쓴다
4. 이 문서 §4.3 표를 갱신한다

## 6. 목업이 검증하는 것

| 항목 | 근거 |
|---|---|
| 수집 스크립트 구문·파이프라인이 실제 PowerShell 5.1에서 동작한다 | 스크립트 **원본**을 그대로 실행 |
| `ConvertTo-Json` 실물 출력(배열/단일 객체/null)이 `parse_ps_json`을 통과한다 | `single`·`empty` 시나리오 |
| VMId 없는 VM이 제외되고 건수가 outcome에 남는다 | §8.4 — 중복 레코드 방지 |
| 전원 상태 5종 매핑과 미매핑 값의 `UNKNOWN` 처리 | §8.1 |
| 게스트 3분법("값 없음" vs "수집 불가") | FR-501 |
| 메모리 단위(SCVMM=MB, 경로 A=바이트) 혼동이 없다 | 1024배 오류 방지 |
| 부분 실패가 예외가 아니라 outcome으로 보고된다 | FR-204 |
| SCVMM 아닌 서버 등록이 인증 실패가 아닌 `kind` 오류로 구분된다 | §10 |
| 수집 경로가 쓰기 cmdlet을 호출하지 않는다 | D-005 — 실행 시점 증거 |
| 500대 규모에서 스트리밍·식별자 유일성이 유지된다 | 규모 경로 |

## 7. 목업이 검증하지 **못하는** 것 — 실환경 실측 항목

목업은 WinRM 아래 계층을 대체하지 못한다. 아래는 **실제 SCVMM 없이는 닫히지 않는다.**

| # | 항목 | 연구 노트 |
|---|---|---|
| 1 | WinRM 전송·인증(Kerberos/NTLM/CredSSP), 세션 수명·타임아웃 | §11-14 |
| 2 | 원격 세션에서 `VirtualMachineManager` 모듈 로드 가능 여부 | §11-14 |
| 3 | `VMId`가 실제로 Hyper-V VM GUID인가 | §11-11 |
| 4 | `OperatingSystem.Name`의 갱신 시점 (생성 시 지정값인가 에이전트 갱신값인가) | §11-12 |
| 5 | `IPv4Addresses`의 출처 (KVP 중계인가 VMM IP 풀인가) | §11-13 |
| 6 | `Read-Only Administrator` 역할의 실제 조회 범위 | §11-15 |
| 7 | `VirtualMachineState` 전체 열거값 | §2.3 |
| 8 | `BiosGuid` 속성 존재 여부 (폴스타 매칭 규칙 1) | 계획 14 §15-6 |
| 9 | `ProductVersion`·`UserName` 속성명 | §2.3 |

**실측 결과는 `docs/04_field_validation.md`에 기록하고, 목 fabric의 데이터를 실제 값으로
교정한다.** 목업이 실환경과 어긋난 채로 남으면 테스트가 통과해도 의미가 없다.
