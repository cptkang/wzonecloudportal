# 00. 클라우드 포탈 조사 노트

> 조사일: 2026-08-06
> 목적: 요건 정의서(`spec.md`) 작성 근거 확보 및 구현 시 기술 참고
> 조사 방법: 웹 검색 및 공식 문서 확인. 각 항목의 출처는 §12에 정리

## 0. 이 문서의 사용법

| 언제 | 누가 | 무엇을 위해 |
|---|---|---|
| 요건 해석이 모호할 때 | requirements-analyst | "이 요건이 왜 있는가"를 §10 추적표로 역추적 |
| 계획 수립 시 | research-planner | §4·§5의 수집 API·속성명을 설계 입력으로 사용. §11의 미검증 항목은 직접 검증 |
| 어댑터 구현 시 | implementer | §4·§5의 API·클래스·속성명, §6의 수집 제약 |
| 검증 시 | verifier | §6·§8의 실패 조건을 테스트 케이스로 |
| 화면 디자인 시 | 디자인 담당 | §13 Claude Design 도구 조사 — 접근 경로·핸드오프·한계 |

**주의**: 이 문서는 **조사 시점(2026-08-06)의 2차 자료 요약**이다.
구현 전에 실제 환경과 라이브러리 버전으로 재확인해야 하며, §11에 검증이 특히 필요한 항목을 모아 두었다.

---

## 1. 조사 범위

| # | 조사 주제 | 목적 |
|---|---|---|
| 1 | CMDB / ITIL 구성 항목(CI) 원칙 | 인벤토리 데이터 모델의 개념적 토대 |
| 2 | CMDB 식별·조정(I&R) 규칙 | 다중 수집 소스의 중복·충돌 처리 |
| 3 | RVTools 속성 집합 | 실무에서 쓰이는 vSphere 인벤토리 표준 항목 |
| 4 | pyVmomi / vSphere API 수집 | vCenter 수집 구현 방식 |
| 5 | vSphere PropertyCollector | 대규모 환경 성능 |
| 6 | Hyper-V PowerShell / WMI 수집 | Hyper-V 수집 구현 방식 |
| 7 | VMware Tools 의존성 | 게스트 정보 수집 한계 |
| 8 | 다중 vCenter / Hyper-V 관리 | 통합 관리 아키텍처 |
| 9 | 좀비 VM·고아 VMDK·스냅샷 | 운영 리포트 항목 도출 |
| 10 | 구성 드리프트·변경 이력 | 변경 추적 요건 |

---

## 2. CMDB·ITIL 원칙 (요건의 개념적 토대)

### 2.1 구성 항목(CI)의 정의

ITIL 4는 CI를 "IT 서비스를 제공하기 위해 관리되어야 하는 모든 구성요소"로 정의하며, **가상 머신은 명시적으로 CI의 예시**에 포함된다.
CMDB는 하드웨어, 소프트웨어, 네트워크, 가상 머신, 클라우드 서비스와 **그 사이의 관계**를 저장하는 중앙 저장소다.

### 2.2 CI 속성

각 CI 레코드는 자산 유형, 소유자, 상태, 위치, 버전/모델, 그리고 **다른 CI와의 관계**를 속성으로 갖는다.
가상 머신 인벤토리에 특화된 속성으로는 소유자/팀, 환경(운영/개발), 중요도, 수명주기 상태, 컴플라이언스 범위,
그리고 `CostCenter`, `Service`, `Tier` 같은 태그가 언급된다.

> **핵심 구분**: 자산 인벤토리는 "우리가 무엇을 보유하는가"에 답하고,
> CMDB는 "무엇이 무엇을 지탱하는가, 누가 소유하는가, 건드리면 무엇이 깨지는가"에 답한다.
> **인벤토리는 목록이고, CMDB는 관계와 수명주기 맥락을 가진 모델이다.**

→ 이 프로젝트는 단순 목록을 넘어 **관계(FR-306, FR-409)와 소유자·수명주기 메타데이터(FR-6xx)** 를 포함하므로 CMDB 성격에 해당한다.

### 2.3 CMDB 운영 원칙

- 각 CI를 주요 속성과 함께 문서화한다.
- **갱신이 발생하면 기존 CI 레코드를 수정한다. 중복을 만들지 않는다.**
- **사용 중지된 CI는 제거한다.** stale 데이터가 서비스 맵을 오염시키는 것을 막기 위함이다.

→ FR-303(중복 자원 방지), FR-307(삭제 자원 처리)의 근거.

### 2.4 식별 규칙 (Identification Rules)

식별 규칙은 CI를 **고유하게 식별하는 속성 집합**을 지정한다. 규칙에는 **우선순위**가 있어 높은 것부터 평가된다.
규칙은 독립적(다른 CI와 무관하게 식별)이거나 종속적(상위 CI를 먼저 식별한 뒤 그 안에서 식별)일 수 있다.

→ FR-302의 3단계 우선순위(연결ID+고유ID → BIOS UUID → MAC+이름) 설계 근거.
→ 연결 ID가 1순위 키에 들어가므로 **연결 ID 불변성(FR-110)이 전제**가 된다.

### 2.5 조정 규칙 (Reconciliation Rules)

조정 규칙은 **여러 소스가 같은 CI 속성을 갱신할 때 누가 이기는지**를 정한다.
특정 데이터 소스를 특정 CI 유형·속성 집합에 대해 **권위 있는 소스(authoritative source)** 로 지정하고,
그 소스만 해당 속성을 쓸 수 있게 한다.

여러 소스를 조정 없이 사용하면 중복 레코드와 불일치 위험이 커진다.

→ FR-304(속성 출처 우선순위)의 근거.
→ 이 프로젝트에서 권위 있는 소스 지정:
> - 게스트 OS·IP: 도구 감지값(VMware Tools / KVP) > VM 구성값
> - 소유자·환경·용도: **포탈 입력이 권위 있는 소스이며 수집 데이터가 덮어쓸 수 없다**

---

## 3. 자원 인벤토리 표준 속성 집합 (RVTools)

RVTools는 vSphere 인벤토리 리포팅의 사실상 표준 도구로, 탭 구성이 곧 실무에서 필요한 속성 집합이다.
**§2 속성 카탈로그(`spec.md`)는 이 구성을 기준으로 작성했다.**

| 탭 | 수집 정보 |
|---|---|
| **vInfo** | VM 목록 전반 — 전원 상태, CPU 수, 메모리 할당, 운영체제, **VMware Tools 상태**, 하드웨어 버전 |
| **vCPU** | 가상 소켓 수, 소켓당 코어 수, CPU 예약/제한 |
| **vMemory** | 메모리 할당, 벌루닝 통계, 스왑 활동 |
| **vDisk** | `Thin` 여부(True/False), **Provisioned MB vs In Use MB** (Thick 프로비저닝 판별) |
| **vNetwork** | 어댑터 타입, MAC 주소 타입 |
| **vHost** | **서비스 태그(시리얼 번호)**, OEM 고유 문자열 |
| **vDatastore** | 데이터스토어에 연결된 호스트 목록 |

**활용 포인트**: vInfo의 vCPU·vMemory 컬럼을 실제 사용 패턴과 비교하면 **과할당(overprovisioned) VM을 식별**할 수 있다.

> **자료 신뢰도 주의**: RVTools 공식 README(GitHub)와 튜토리얼 사이트는 각각 404/403으로 직접 확인에 실패했다.
> 위 표는 2차 자료(4sysops, Nutanix sizing 문서 등)를 종합한 것이다.
> 정확한 전체 컬럼 목록이 필요하면 RVTools를 직접 실행하여 확인할 것 (§11-1).

---

## 4. vCenter 수집 기술

### 4.1 수집 가능한 인벤토리 객체

pyVmomi로 접근 가능한 핵심 객체: **Datacenter, Cluster, VirtualMachine, HostSystem, Datastore, Network**.

인벤토리 디스커버리로 얻을 수 있는 정보: 운영체제 유형, 스토리지, 코어 수 등 기본 정보와 CPU·메모리·디스크 사용량.
**게스트 레벨 정보(시스템 정보, 네트워크 정보)는 VMware Tools를 통해** 수집한다.

### 4.2 PropertyCollector — 대규모 환경 필수 기법

`RetrievePropertiesEx`는 다음 구조로 동작한다:

```
ContainerView          — 특정 폴더/인벤토리 범위의 관리 객체 목록 (VM만, Host만 등 타입 지정 가능)
  └ PropertyFilterSpec
      ├ ObjectSpec     — 시작 객체
      ├ TraversalSpec  — 인벤토리 순회 경로
      └ PropertySpec   — 가져올 속성 목록 (필요한 것만)
```

**페이징**: 결과가 남아 있으면 응답에 토큰이 포함된다. 토큰이 더 이상 오지 않을 때까지 반복 호출한다.

**이점**: 자원을 하나씩 순회하며 개별 속성을 읽는 방식 대비 **왕복 횟수를 크게 줄인다.**
대규모 환경에서 특정 객체 타입만 targeting할 수 있어 ContainerView가 특히 유용하다.

**최신 동향**: vSphere 8.0 Update 1의 VI JSON 프로토콜로 PropertyCollector·ContainerView 사용이 더 개선되었다.
→ 단, **이 프로젝트는 VI JSON을 쓰지 않는다.** 지원 하한이 6.5이고 VI JSON은 8.0 U1+ 전용이다 (D-010, `spec.md` CST-10).

**PropertyCollector는 SOAP(vim25) 전용 메커니즘이다.** vSphere Automation API(REST)에는 대응 기능이 없어
자원 수만큼 왕복이 발생한다. 이것이 vCenter 접근을 SOAP 단일 경로로 확정한 결정적 근거다 (D-010).

→ FR-203, NFR-107, D-007의 근거. **구현 시 이 방식을 반드시 사용할 것** (`.claude/agents/implementer.md` 수집 어댑터 규칙).

### 4.3 구현 시 참고할 속성 경로

`spec.md` §2.2의 "vCenter 출처" 열에 속성별 경로를 정리했다. 주요 항목:

```
config.uuid              BIOS UUID
config.hardware.numCPU   vCPU 수
config.hardware.memoryMB 메모리(MB)
config.version           VM 하드웨어 버전
config.firmware          BIOS / UEFI
config.guestFullName     게스트 OS (구성값 — 실제와 다를 수 있음)
config.annotation        노트
runtime.powerState       전원 상태
runtime.host             소속 호스트
runtime.connectionState  연결 상태
guest.guestFullName      게스트 OS (도구 감지값 — 권위 있는 값)
guest.hostName           게스트 호스트명
guest.net[].ipAddress    IP 주소 목록
```

---

## 5. Hyper-V 수집 기술

### 5.1 PowerShell 방식

```powershell
# VM의 IP 주소 — NetworkAdapters 속성 경유
Get-VM DC | Select-Object -ExpandProperty NetworkAdapters | Select-Object IPAddresses

# 네트워크 어댑터 구성 (VM명, 스위치명, IP)
Get-VM -Name VM1 | Select -ExpandProperty NetworkAdapters | Select VMName, SwitchName, IPAddresses
```

`Get-VMNetworkAdapter` cmdlet은 어댑터에 할당된 `IPAddress` 속성을 포함한다.

### 5.2 WMI 방식 (`root\virtualization\v2`)

```
Msvm_ComputerSystem            VM 자체 (Name 속성이 VM GUID)
  └ GetRelated('Msvm_SyntheticEthernetPort')   네트워크 어댑터 (이름, MAC)
Msvm_VirtualSystemSettingData  VM 설정 (BIOSGUID 등)
Msvm_ProcessorSettingData      vCPU (VirtualQuantity)
Msvm_MemorySettingData         메모리 (VirtualQuantity)
Msvm_StorageAllocationSettingData  스토리지 할당
```

### 5.3 KVP (Key-Value Pair) — 게스트 정보의 핵심

Windows Server 2012 Hyper-V부터 `Get-VMNetworkAdapter`가 도입되었고,
**IP 주소 정보는 KVP(키-값 쌍) 통합 구성요소를 통해** 취득한다.

KVP로 얻을 수 있는 게스트 속성:

```
FullyQualifiedDomainName   게스트 FQDN
OSName                     게스트 OS 이름
OSVersion                  게스트 OS 버전
NetworkAddressIPv4         IPv4 주소
NetworkAddressIPv6         IPv6 주소
```

→ **KVP는 Hyper-V에서 게스트 OS·IP를 얻는 유일한 경로**이며, 통합 서비스가 동작해야 한다.
vCenter의 VMware Tools와 정확히 같은 위치의 의존성이다 (§6).

---

## 6. 데이터 수집 제약 — 도구 의존성 (가장 중요한 제약)

### 6.1 VMware Tools 미설치 시

- **게스트 하트비트와 관리 정보(IP 주소 포함)가 vCenter 요약 탭에서 누락**된다.
- `guestinfo`의 `ipAddress` 필드 정의 자체가 *"게스트 OS에 할당된 주 IP 주소, **알 수 있는 경우**"* 다.
- 게스트 OS 정보도 제대로 채워지지 않아 인벤토리에서 "OS unknown" 상태가 된다.

### 6.2 Linux 게스트 추가 제약

Linux VM은 **IP Address origin 속성이 unset으로 남는 경우**가 있어, 게스트가 DHCP를 쓰는지 고정 IP를 쓰는지 알 수 없다.
Windows VM은 VMware Tools가 설치되어 있으면 이 속성이 정상적으로 채워진다.

### 6.3 도구 상태의 다양성

VMware Tools 상태는 단순히 설치/미설치가 아니라 `Unmanaged`, `Guest Managed` 등으로 보고될 수 있다.
또한 Tools를 설치했는데도 vCenter가 미설치로 표시하는 알려진 버그(13.0.1 업그레이드 후)도 존재한다.

### 6.4 요건에 미친 영향

이 제약 때문에 **"값 없음"과 "수집 불가"를 반드시 구분**해야 한다.
IP가 비어 있을 때 그것이 *IP가 없는 VM*인지 *IP를 알 수 없는 VM*인지 구분되지 않으면 인벤토리 신뢰도가 무너진다.

→ CST-02, FR-501(수집 불가 명시), FR-504(도구 미설치 자원 목록), FR-505(데이터 품질 지표), D-006의 근거.

---

## 7. 다중 인스턴스 관리

### 7.1 vCenter — Enhanced Linked Mode

vCenter Server의 Linked Mode는 여러 vCenter 인스턴스에 걸친 가시성을 제공하여, **한 번 로그인으로 모든 vCenter의 인벤토리를 조회·검색**할 수 있다. Linked Mode 배포에서는 역할, 권한, 라이선스가 인프라 전체에 복제된다.
Embedded PSC를 사용하는 Enhanced Linked Mode는 외부 PSC나 로드밸런서 없이 여러 vCenter를 연결한다.

> **이 프로젝트에서의 판단**: ELM은 같은 SSO 도메인에 묶인 vCenter들에만 적용된다.
> 조직·망이 분리된 독립 vCenter는 ELM으로 묶이지 않으므로, **포탈이 각 vCenter에 개별 연결하는 방식(FR-102)이 필요**하다.
> ELM 환경이라면 하나의 연결로 여러 vCenter 인벤토리가 보일 수 있어 **중복 수집 위험**이 있다 (FR-308의 배경).

### 7.2 Hyper-V — SCVMM 유무에 따른 차이

- Hyper-V Manager, Failover Cluster Manager, Windows Admin Center는 중소 규모 관리에 무료로 제공된다.
- **SCVMM은 SCVMM이 관리하는 모든 호스트와 클러스터에 대한 fabric 전체 가시성**을 제공한다.
- Failover Cluster는 클러스터 내 모든 노드에 대한 가시성과 마이그레이션 추적을 제공한다.
- 다수 Hyper-V 호스트의 인벤토리 수집에서 **SCVMM이 Microsoft의 기본 해법**이며, 없으면 에이전트리스 도구를 쓰거나 호스트별로 접근해야 한다.

→ CST-03, CST-09, §2.7의 연결 유형(`hyperv-host` / `hyperv-cluster` / `scvmm`) 구분 근거.
**2026-08-07 SCVMM 도입 확정** — 위 조사 내용이 그대로 채택되어 SCVMM이 주 수집 경로가 되었다 (D-012).

### 7.3 WinRM 인증 방식

Hyper-V 원격 관리는 WinRM을 사용하며 인증 방식(NTLM / Kerberos / CredSSP)이 환경에 따라 달라진다.
도메인 미가입 호스트나 이중 홉(second-hop) 시나리오에서는 대상 서버에 TrustedHosts 등록이나 CredSSP 활성화 같은 **추가 설정이 필요**하다.

→ §2.7의 인증 방식 선택 항목, FR-103(연결 유형별 입력 폼), CST-06의 근거.

---

## 8. 운영 인사이트 — 낭비 자원 식별

### 8.1 좀비 VM (Zombie / Orphaned / Stale VM)

더 이상 사용되지 않지만 가상화 환경에서 자원을 계속 점유하는 VM.
활성 워크로드가 아니라서 **눈에 띄지 않은 채 스토리지·메모리·CPU를 소비**하며 데이터센터 운영 효율을 떨어뜨린다.

### 8.2 고아 VMDK (Orphaned VMDK)

연결된 VM이 삭제된 뒤에도 남아 있는 가상 디스크 파일. 사용 가능한 디스크 공간을 잠식하여 자원 경합을 유발한다.

### 8.3 오래된 스냅샷

스냅샷 delta 파일이 누적되어 공간을 점유한다. 현재 VM에 연결되지 않은 스냅샷 디스크 파일을 찾아 식별한다.

### 8.4 도구의 한계 (중요)

VMware Aria Operations는 고아 디스크를 **보수적으로** 리포팅하며, **GUI에서 직접 삭제·회수하는 기능은 제공하지 않는다.**
목록에 오른 고아 디스크는 **vSphere Client의 데이터스토어 브라우저에서 수동 검증**해야 한다.

→ **이 프로젝트의 설계 판단**: 성숙한 상용 도구조차 자동 삭제를 제공하지 않고 수동 검증을 요구한다.
잘못 식별된 "고아" 디스크를 삭제하면 복구 불가능한 데이터 손실이 발생하기 때문이다.
따라서 포탈은 **식별·보고까지만 수행하고 정리 작업은 하지 않는다** (FR-803, FR-804, CST-01).

---

## 9. 구성 드리프트·변경 이력

### 9.1 변경 추적의 구성 요소

구성 관리 도구는 **무엇이 존재하는지, 어떻게 구성되었는지, 무엇이 바뀌었는지, 누가 바꿨는지,
그리고 그것이 바뀌어야 했던 것인지**를 기록한다.

### 9.2 변경 이력 요건

- 구성 변경 이력을 유지하여 컴플라이언스·문제 해결을 위한 **감사 추적(audit trail)** 을 제공한다.
- 시간에 따른 변경 추적으로 **시스템적 문제를 시사하는 패턴**을 식별할 수 있다.

### 9.3 감사 로그에 담아야 할 항목

영향받은 자원, 감지된 드리프트, 취해진 조치, 승인자, 결과.

→ FR-701~FR-706(변경 이력), FR-1004(감사 로그)의 근거.
> 단, 이 포탈은 읽기 전용이므로 "조치·승인자"는 자원 변경이 아니라 **포탈 내 작업(연결 등록·수정, 메타데이터 변경)** 에 적용한다.

---

## 10. 조사 → 요건 추적표

구현 중 "이 요건이 왜 있는가"를 물을 때 이 표를 역참조한다.

| 조사 결과 | 반영된 요건 | 관련 결정 |
|---|---|---|
| CI는 중복 생성 없이 기존 레코드를 갱신 (§2.3) | FR-303 중복 자원 방지 | D-006 |
| 식별 규칙은 속성 우선순위를 갖는다 (§2.4) | FR-302 CI 식별 규칙 (3단계) | D-006 |
| 식별 1순위에 연결 ID 포함 → 불변 필요 (§2.4) | FR-110 연결 식별자 불변성 | D-006, D-008 |
| 조정 규칙 = 권위 있는 소스 지정 (§2.5) | FR-304 속성 출처 우선순위, FR-602 메타데이터 보존 | D-006 |
| 사용 중지 CI 제거, stale 데이터 오염 방지 (§2.3) | FR-307 삭제 자원 유예 처리 | D-006 |
| CMDB는 관계 모델 (§2.1) | FR-306 자원 간 관계, FR-409 관계 탐색 | — |
| RVTools 속성 집합 (§3) | `spec.md` §2 속성 카탈로그 전체 | — |
| PropertyCollector + 페이징 (§4.2) | FR-203 대규모 환경 수집, NFR-107 | D-007 |
| 조회를 하이퍼바이저 직접 호출로 하면 부하 (§4.2) | NFR-101 조회 1초, NFR-301 부분 실패 | D-007 |
| KVP가 Hyper-V 게스트 정보의 유일 경로 (§5.3) | `spec.md` §2.2 Hyper-V 출처 열 | — |
| VMware Tools 없으면 IP·OS 수집 불가 (§6.1) | CST-02, FR-501 수집 불가 명시 | D-006 |
| Linux 게스트 IP origin 미제공 (§6.2) | CST-02 주석, FR-501 | — |
| 도구 상태가 다양함 (§6.3) | FR-504 도구 미설치·구버전 목록 | — |
| 독립 vCenter는 ELM으로 안 묶임 (§7.1) | FR-102 다중 연결 지원 | — |
| ELM 환경의 중복 수집 위험 (§7.1) | FR-308 연결 간 중복 감지, FR-105 중복 등록 방지 | D-008 |
| SCVMM 유무로 수집 방식 상이 (§7.2) | CST-03, CST-09, §2.7 연결 유형 구분 | — |
| WinRM 인증 방식이 환경 의존 (§7.3) | FR-103 연결 유형별 입력 폼, CST-06 | D-008 |
| AD 계정 잠금 위험 (§7.3 관련 실무 판단) | FR-114 인증 실패 시 재시도 중단, CST-05 | D-008 |
| Claude Design 핸드오프가 구조화 spec 전달 (§13.5) | FR-1212 구현 전 디자인 확정 | D-009 |
| 범용 디자인 도구는 조작 UI를 기본 포함 (§13.7) | FR-1206 자원 변경 UI 미제공 | D-009 |
| 좀비 VM·고아 VMDK·스냅샷이 낭비 요인 (§8) | FR-803 스냅샷 현황, FR-804 유휴 자원, FR-805 용량 현황 | — |
| 상용 도구도 자동 삭제 미제공, 수동 검증 요구 (§8.4) | CST-01 (식별·보고만 수행) | D-005 |
| 변경 이력 = 무엇이·언제·누가 (§9.1) | FR-701·FR-702 속성 변경 이력 | — |
| 감사 추적 항목 (§9.3) | FR-1004 감사 로그 | D-008 |

---

## 11. 미검증 항목 — 구현 전 반드시 확인할 것

| # | 항목 | 왜 검증이 필요한가 | 확인 방법 |
|---|---|---|---|
| 1 | RVTools 전체 컬럼 목록 | 공식 README(404)·튜토리얼(403) 접근 실패로 2차 자료 기반 | RVTools 직접 실행 또는 공식 배포판 문서 확인 |
| 2 | pyVmomi 속성 경로의 버전별 차이 | vSphere 버전에 따라 속성 존재 여부·형식이 다를 수 있음 | 대상 vCenter 버전에서 실제 조회. **절반 해소 (2026-08-19)** — `plans/04` §5의 속성 경로가 **pyVmomi 9.1.0.0 바인딩에 전부 존재함을 확인**(상한 쪽). 하한(6.5) 실측은 잔여 (D-020) |
| 3 | 대규모 환경 수집 소요 시간 | 관리 규모(NFR-104)가 미확정이라 목표치를 못 세움 | 규모 확정 후 파일럿 측정 |
| 4 | Hyper-V KVP 속성의 게스트 OS별 가용성 | Linux 게스트의 KVP 지원 범위가 Windows와 다를 수 있음 | 대상 환경의 Linux VM에서 실측 |
| 5 | WinRM 인증 방식별 필요 설정 | 도메인 구성에 따라 TrustedHosts·CredSSP 설정이 다름 | 대상 Hyper-V 환경 관리자와 확인. **라이브러리 측은 확인됨 (2026-08-14, pypsrp 0.9.1)**: CredSSP는 `pypsrp[credssp]`(requests-credssp), Kerberos는 `pypsrp[kerberos]`(pyspnego[kerberos]) extra 필요. 비동기 API 없음 → 전부 `asyncio.to_thread` (D-018) |
| 6 | Failover Cluster 인벤토리 조회 방식 | 클러스터 단위 조회 API를 직접 확인하지 못함 | Failover Clustering PowerShell 모듈 문서 확인 |
| 7 | ~~vSphere Tags 수집 방법~~ | **해소됨 (2026-08-06)** — pyVmomi는 SOAP(vim25) 전용이고 Tags는 vSphere Automation API(REST) 소관임이 확인됨. **수집하지 않기로 결정** (D-010). Custom Attributes는 SOAP `CustomFieldsManager`로 수집 | — |
| 8 | 자격증명 암호화 키 관리 방식 | 조사 범위에 없었음. 배포 환경에 따라 선택지가 다름 | NFR-208 `[TODO]` 확정 시 별도 조사 |
| 9 | **pyVmomi의 vSphere 6.5 호환 범위** | 지원 하한을 6.5로 확정했으나(CST-10) 6.5는 VMware EOL 버전이라 최신 pyVmomi가 호환을 보증하지 않을 수 있음 | 대상 환경의 실제 버전 분포 확인 후 pyVmomi 버전 고정 (`plans/01`). **근거 확정 (2026-08-19)** — Broadcom 호환 정책은 **직전 4개 릴리스**이므로 6.5는 범위 밖. PyPI 최신은 9.1.0.0이고 **`pyproject.toml`은 아직 무핀**이다. VCF 9.0부터 독립 SDK 배포 중단(VCF Python SDK로 통합). 상세 `plans/04` §14.2, D-020 |
| 10 | Read-Only 역할에서 `customValue`·`customFieldsManager.field` 조회 가능 여부 | 조회 불가면 FR-606의 남은 절반(Custom Attributes)도 수집 불가 | Read-Only 계정으로 실측 (`plans/04` §5.2) |
| 11 | **SCVMM `Get-SCVirtualMachine`의 `VMId` 존재 여부** | `native_id`의 근거다. 비어 있는 VM이 있으면 CI 식별이 깨진다 (D-012) | SCVMM에서 실측 (`plans/05` §7.2·§8.4) |
| 12 | **SCVMM `OperatingSystem.Name`의 갱신 시점** | VM 생성 시 지정값인지 에이전트 갱신값인지에 따라 OS 출처 판정(FR-304)이 달라진다 | Tools 미설치 VM과 설치 VM을 비교 실측 |
| 13 | **SCVMM `IPv4Addresses`의 출처** | KVP 중계인지 VMM IP 풀 값인지에 따라 "수집 불가" 판정 기준이 달라진다 (FR-501) | 통합 서비스를 끈 VM에서 값이 남는지 확인 |
| 14 | **원격 세션에서 `VirtualMachineManager` 모듈 로드 가능 여부** | 불가하면 경로 B 전체가 성립하지 않는다 | SCVMM 서버에 WinRM 접속 후 `Import-Module` (`plans/05` §4.2) |
| 15 | **`Read-Only Administrator` 역할의 조회 범위** | 이 역할로 VM·호스트·스토리지를 모두 읽지 못하면 읽기 전용 계정 전제가 무너진다 (D-012) | 해당 역할 계정으로 §10 권한 프로브 실행 |
| 16 | ~~경로 A의 읽기 전용 계정 실현 방법~~ | **해소됨 (2026-08-07)** — SCVMM 도입 확정으로 경로 A 대상이 소수가 되어 **JEA 제약 세션**을 채택 (D-012 결정 7). 미해결로 남은 것은 아래 17·18 | — |
| 17 | **`pypsrp`의 JEA 세션 구성 지원** | `RunspacePool`에 `configuration_name`을 지정해 JEA 엔드포인트에 붙을 수 있어야 한다 | **절반 해소 (2026-08-14)** — pypsrp 0.9.1의 `RunspacePool.__init__`이 `configuration_name` 파라미터(기본 `Microsoft.PowerShell`)를 지원함을 시그니처로 확인 (D-018). 인증 예외도 `pypsrp.exceptions.AuthenticationError` 타입 매칭으로 확정. **실제 JEA 엔드포인트 접속·허용 함수 외 차단 확인은 실환경 실측 필요** (`plans/05` §12-12) |
| 18 | **JEA 함수 반환값의 직렬화** | 제약 세션은 객체를 역직렬화된 형태로 준다. 함수 안에서 `ConvertTo-Json`까지 마쳐 문자열을 반환하면 회피 가능한지 확인 | JEA 세션에서 수집 함수 실행 후 출력 형태 확인 |
| 19 | **최신 VCF(9.x) 환경에서의 vCenter 수집 성립 여부** | 조사·계획이 지원 **하한(6.5)** 기준으로만 작성되어 상한 검증이 전무했다. vCenter 9.0은 **사용자명+암호 단독 로그인을 차단**하고, NSX 세그먼트·vSAN·제품명(ESX)이 8.x와 다르다 | 대상 환경 버전 분포 확인 후 `plans/ROADMAP.md` §15.5의 7개 항목 실측 (`plans/04` §14, D-020) |

---

## 12. 출처

### CMDB · ITIL
- [Atlassian — What Is CMDB](https://www.atlassian.com/itsm/it-asset-management/cmdb)
- [Cloudaware — CMDB CI Explained](https://cloudaware.com/blog/cmdb-ci/)
- [Cloudaware — ITIL CMDB 실무 가이드](https://cloudaware.com/blog/itil-cmdb/)
- [Virima — CMDB Asset Management vs ITAM](https://virima.com/blog/cmdb-asset-management-vs-itam-key-differences-explained)
- [ServiceNow — CMDB Identification & Reconciliation](https://www.servicenow.com/community/cmdb-articles/cmdb-identification-reconciliation/ta-p/2301712)
- [ServiceNow — 식별·조정 규칙과 규칙 간 충돌](https://www.servicenow.com/community/cmdb-articles/cmdb-understanding-identification-and-reconciliation-rules-and/ta-p/3520826)

### vSphere 수집
- [Broadcom — Using the PropertyCollector with RetrievePropertiesEx](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere-sdks-tools/8-0/web-services-sdk-programming-guide/property-collector/using-the-propertycollector-with-retrievepropertiesex.html)
- [VMware — Efficient Data Retrieval with PropertyCollector and ContainerView (2024-10)](https://blogs.vmware.com/cloud-foundation/2024/10/21/efficient-data-retrieval-with-vi-json-api-propertycollector-and-containerview/)
- [PropertyCollector Example (RetrievePropertiesEx)](https://vdc-repo.vmware.com/vmwb-repository/dcr-public/c9f497d8-4819-4876-a6d5-7313453dd103/c36d536f-337a-4586-8fbf-944d5f20cb66/doc/PG_PropertyCollector.7.5.html)
- [pyVmomi tutorial — core vCenter inventory objects](https://vthinkbeyondvm.com/pyvmomi-tutorial-how-to-get-all-the-core-vcenter-server-inventory-objects-and-play-around/)
- [Google Cloud — VMware VM 디스커버리 수집 방식](https://docs.cloud.google.com/migration-center/docs/discover-vmware-vms)

### VMware Tools 제약
- [Broadcom KB — VMware Tools 상태가 Unmanaged/Guest Managed로 보고됨](https://knowledge.broadcom.com/external/article/343269/vsphere-client-reports-the-status-of-vmw.html)
- [open-vm-tools Issue #694 — Linux 게스트의 IP origin 미설정](https://github.com/vmware/open-vm-tools/issues/694)
- [Broadcom KB — Tools 설치 실패로 IP 구성 손실](https://knowledge.broadcom.com/external/article/391287/vmware-tools-installation-fails-with-err.html)

### Hyper-V 수집
- [Microsoft Learn — Get-VMNetworkAdapter](https://learn.microsoft.com/en-us/powershell/module/hyper-v/get-vmnetworkadapter?view=windowsserver2025-ps)
- [Microsoft Learn — Hyper-V WMI로 네트워크 어댑터 정보 조회](https://learn.microsoft.com/en-us/answers/questions/135604/hyper-v-fetch-network-adapter-related-information()
- [Microsoft Archive — Retrieving the IP Address Of A VM In Hyper-V (KVP)](https://learn.microsoft.com/hu-hu/archive/blogs/taylorb/retrieving-the-ip-address-of-a-vm-in-hyper-v)
- [Redmondmag — Retrieving Detailed Information About Hyper-V VMs](https://redmondmag.com/articles/2018/08/08/retrieving-detailed-info-about-hyper-v-vms.aspx)

### 다중 인스턴스 관리
- [O'Reilly — Managing inventory, hosts, VMs via vCenter](https://www.oreilly.com/library/view/vmware-vsphere-5-5/9781784398750/ch02s09.html)
- [TechTarget — SCVMM vs Hyper-V Manager](https://www.techtarget.com/searchitoperations/tip/SCVMM-vs-Hyper-V-Manager-Which-tasks-are-best-suited-to-each)
- [Veeam — SCVMM, Cluster, or Standalone](https://community.veeam.com/blogs-and-podcasts-57/scvmm-cluster-or-standalone-what-changes-when-you-pick-each-one-13215)
- [Nakivo — What Is SCVMM](https://www.nakivo.com/blog/what-is-system-center-virtual-machine-manager-scvmm/)

### 인벤토리 속성 표준 (RVTools)
- [4sysops — What's new in RVTools 4 for VMware vSphere](https://4sysops.com/archives/whats-new-in-rvtools-4-for-vmware-vsphere/)
- [Nutanix Sizing — Analyzing RVTools Data](https://sizing-workshop.readthedocs.io/en/latest/datacollection/rvtools/rvtools.html)
- [RVTools 공식 사이트](https://www.rvtools.net/)

### 낭비 자원 · 변경 추적
- [Broadcom KB — Zombie/Orphaned 디스크 식별 (Aria Operations)](https://knowledge.broadcom.com/external/article/383876/how-to-identify-zombie-orphaned-disks-i.html)
- [Broadcom KB — Zombie files and Leftover VM on Datastore](https://knowledge.broadcom.com/external/article/404094/zombie-files-and-leftover-vm-on-datastor.html)
- [SolarWinds — VM Sprawl Control](https://www.solarwinds.com/virtualization-manager/use-cases/vm-sprawl-control)
- [ITU Online — What Is a Zombie VM](https://www.ituonline.com/tech-definitions/what-is-a-zombie-vm/)
- [Microsoft Learn — Azure Change Tracking and Inventory](https://learn.microsoft.com/en-us/azure/azure-change-tracking-inventory/overview-monitoring-agent)
- [Open-AudIT — Configuration Management & Change Detection](https://open-audit.com/network-configuration-management/)

---

## 13. Claude Design — UI 디자인 도구 조사

> 조사일: 2026-08-06
> 목적: 웹 UI 디자인 방식 확정 (`spec.md` FR-1212, `docs/02_decision.md` D-009, 계획 11 Part A)

### 13.1 개요

Anthropic Labs가 2026년 4월 출시한 **프로토타이핑 워크스페이스**다.
텍스트 프롬프트를 동작하는 UI 초안으로 변환하며, 결과물은 HTML/CSS/JS로 라이브 프리뷰에 렌더링된다.

이미지 생성기가 아니라 **프로토타이핑 엔진**이다. 랜딩 페이지, 대시보드 목업, 인터랙티브 슬라이드 등을 만든다.

### 13.2 접근 경로와 전제

| 항목 | 내용 | 출처 |
|---|---|---|
| 접근 | **웹 `claude.ai/design` 또는 Claude Desktop 사이드바** | 공식 지원 문서 |
| 미지원 | 모바일 | 공식 |
| 플랜 | Pro / Max / Team / Enterprise (베타) | 공식 |
| Enterprise | **기본 비활성 — 관리자가 조직 설정에서 활성화** | 공식 |

> **2차 자료 불일치 주의**: 일부 가이드는 "데스크톱 앱에서는 동작하지 않고 브라우저만 가능"이라고 기술한다.
> **공식 지원 문서는 Claude Desktop 사이드바 접근을 명시**하므로 공식을 기준으로 한다.
> 초기 웹 전용에서 데스크톱으로 확대된 것으로 보인다.

### 13.3 편집 방식

- 자연어 대화로 수정
- 인라인 코멘트
- 캔버스에서 직접 편집
- 간격·레이아웃 조정용 AI 생성 슬라이더

### 13.4 디자인 시스템 임포트

온보딩 시 **기존 코드베이스와 디자인 파일을 인제스트**하여 팀의 색상·타이포그래피·컴포넌트 라이브러리를
신규 작업에 자동 적용한다. 품질은 소스 품질에 의존한다.

→ 이 프로젝트는 신규라 최초에는 임포트할 자산이 없다. Wave 4 이후 `static/` 연결이 가능하다 (계획 11 §5.3).

### 13.5 내보내기 — Claude Code 핸드오프가 핵심

| 옵션 | 내용 |
|---|---|
| **Handoff to Claude Code** | **로컬 코딩 에이전트 또는 Claude Code Web으로 전달** |
| Standalone HTML | 자체 호스팅용 인터랙티브 프로토타입 |
| `.zip` | 원시 에셋 |
| PDF / PPTX | 문서·발표용 |
| 내부 URL | 조직 내 공유 (보기·코멘트·편집 권한) |
| 외부 연동 | Canva, Adobe, Gamma, Vercel 등 |

**핸드오프 번들 구성** (2차 자료):
- 컴포넌트 구조 — machine-readable spec
- **캔버스에서 실제 사용된 디자인 토큰**
- 레이아웃 계층과 관계
- 참조 에셋

> "PNG가 아니다. 플러그인이 필요한 디자인 URL도 아니다. Claude Code가 직접 읽는 spec 파일이다."
> 공식 문서 표현으로는 **"스크린샷에서 새로 시작하는 대신 기존 작업을 이어받는다."**

→ 디자인을 보고 재구성하는 손실이 없다는 점이 이 도구를 채택한 핵심 근거다 (D-009).

### 13.6 알려진 한계

| 항목 | 내용 | 이 프로젝트에서의 대응 |
|---|---|---|
| **토큰 소비** | 한 세션이 Pro 주간 할당량 **50% 이상**을 소비한 사례 보고 | 화면 단위로 분할 진행 (계획 11 §11) |
| 정밀도 | Figma Auto Layout 수준의 세밀함은 아님. "강한 디자인의 80~90%까지 빠르게" | 픽셀 조정은 구현 단계 |
| 리서치 프리뷰 | 인라인 코멘트가 간헐적으로 사라짐 | 중요 피드백 별도 기록 |
| 대규모 코드베이스 | 연결 시 지연 | `static/`만 연결 |
| 다중 편집 | 기본 수준 | 검토는 코멘트 중심으로 |

### 13.7 이 프로젝트 고유의 위험 — 조사에 없는 항목

**범용 디자인 도구는 "가상 머신 관리 대시보드"를 만들면 전원·삭제·스냅샷 버튼을 자연스럽게 포함시킨다.**

이 포탈은 읽기 전용이므로(FR-1206, D-005) 그런 요소가 들어가면 안 되지만,
도구는 그 제약을 알지 못한다. 조사 자료 어디에도 이 문제는 언급되지 않으며,
**프롬프트로 명시하고 화면마다·핸드오프 후에 검증해야 한다** (계획 11 §5.1, §6.1, §8.3).

마찬가지로 이 프로젝트의 핵심 표시 규칙인 "수집 불가 / 해당 없음 / 빈 값" 3분기(FR-1204)도
일반적인 디자인 관행이 아니므로 명시하지 않으면 반영되지 않는다.

### 13.8 관련: Claude Code ↔ Figma 연동

2026년 2월 Figma와 "Code to Canvas" 통합이 발표되었다. Figma MCP 서버를 통해
Claude Code가 디자인 파일을 읽고, 구현된 UI를 Figma 캔버스로 되돌릴 수 있다.
Figma 데스크톱 앱과 Dev/Full 시트가 필요하다.

→ **이 프로젝트에는 채택하지 않는다.** Figma 도입·학습 비용이 발생하고,
Claude Design 핸드오프로 동일 목적을 달성할 수 있다 (D-009 대안 검토).

### 13.9 출처

- [Anthropic 공식 — Get started with Claude Design](https://support.claude.com/en/articles/14604416-get-started-with-claude-design)
- [ShelbyAI — How to Use Claude Design (2026)](https://www.shelby-ai.com/guides/how-to-use-claude-design/)
- [claudefa.st — Claude Design to Claude Code: AI Design Handoff](https://claudefa.st/blog/guide/mechanics/claude-design-handoff)
- [MindStudio — What Is Claude Design?](https://www.mindstudio.ai/blog/what-is-claude-design-anthropic-visual-prototyping)
- [Neowin — Anthropic is turning Claude into a full-blown design tool](https://www.neowin.net/news/anthropic-is-turning-claude-into-a-full-blown-design-tool-with-this-latest-update/)
- [Claude Code 데스크톱 애플리케이션 문서](https://code.claude.com/docs/en/desktop)
- [Figma — Introducing Claude Code to Figma](https://www.figma.com/blog/introducing-claude-code-to-figma/)
- [Figma Help — Claude Code and Figma: Set up the MCP server](https://help.figma.com/hc/en-us/articles/39888612464151-Claude-Code-and-Figma-Set-up-the-MCP-server)
