# 15. 자원(VM) 조회 기능 수정 계획

> 작성일: 2026-08-16 · 대상 단계: **ROADMAP Step 4 (VM 상세 속성 + 검색)**
> 담당 요건: FR-401~409, FR-501·502 · 보완 요건 FR-410~414, FR-506~508 (`docs/01_requirements.md` §3.5·3.6)
> 의존: 계획 02·04·05·06·07·08·11 · 관련 결정: D-006, D-007, D-010, D-011, D-012, D-016
> 선행: Step 2(실환경 실측), Step 3(다중 연결·자동 수집) — **§14 참조**

## 1. 목적

지금 포탈은 **VM이 있다는 것만 보여준다.** 이름·전원·vCPU·메모리·게스트 OS·호스트 MoRef·수집 시각 7개 컬럼이
전부이고, 검색도 상세도 없다. 이 상태로는 인벤토리로 쓸 수 없다 — 운영자가 포탈에 오는 첫 번째 이유인
**"이 IP를 쓰는 VM이 뭐지"** 에 답하지 못하기 때문이다.

이 계획은 두 가지를 한다.

1. **사용자에게 보여줄 정보를 확정한다** (§3) — 목록 컬럼, 상세 항목, 표기 규칙, 하이퍼바이저별 값 출처
2. **그 정보가 흐르도록 수집→저장→조회→화면 전 구간을 수정한다** (§4~§10)

**범위는 VM 자원 하나다.** Host/Cluster/Datastore/Network 자원 조회와 관계 탐색(FR-409)은 Step 6이며,
메타데이터(FR-6xx)·변경 이력(FR-7xx)은 Step 7이다. 이 계획은 **그 자리를 비워 두되 화면과 응답 계약에
자리를 만들어 둔다** (D-011 — 계약은 최종 형태로).

---

## 2. 현재 상태 진단

코드 기준(2026-08-16)이다. 요건이 아니라 **실제로 데이터가 어디서 끊기는지**를 본다.

### 2.1 수집 — 무엇을 가져오는가

| 어댑터 | 파일 | 가져오는 것 | 안 가져오는 것 |
|---|---|---|---|
| vCenter | `vcenter/property_specs.py` | name, instanceUuid, uuid, guestFullName(구성값·도구값), numCPU, memoryMB, powerState, connectionState, host, hostName, toolsStatus, toolsRunningStatus | **`config.hardware.device`(디스크·NIC), `guest.net`(IP), `snapshot`, `config.version`, `config.firmware`, `numCoresPerSocket`, `runtime.bootTime`, `config.annotation`, `config.createDate`, `guest.toolsVersion`** |
| Hyper-V 경로 A | `hyperv/host_scripts.py` | Id, Name, State, ProcessorCount, MemoryAssigned/Startup, Version, Generation, BiosGuid, ComputerName, **KVP OSName·OSVersion·FQDN·IPv4·IPv6**, **Adapters(IPAddresses)** | Disks(`Get-VHD`), Checkpoints(`Get-VMSnapshot`), 어댑터의 MAC·스위치명·연결상태, 동적 메모리 범위, CreationTime |
| Hyper-V 경로 B | `hyperv/scvmm_scripts.py` | Id, Name, State, ProcessorCount, MemoryMB, Version, Generation, OperatingSystem, HostName, **Adapters(IPv4·IPv6)** | VirtualHardDisks, Checkpoints, 동적 메모리, MAC·스위치명, CreationTime |

### 2.2 저장 — 무엇이 남는가

`virtual_machines` 테이블 컬럼은 18개다. `vm_disks`·`vm_adapters`·`vm_adapter_ips`·`snapshots` 테이블은 **없다**.

> **⚠ 이 계획의 출발점 — Hyper-V IP가 저장 단계에서 버려지고 있다**
>
> `host_mapper.map_guest_info()`는 KVP `NetworkAddressIPv4`와 `Get-VMNetworkAdapter`의 IP를 병합해
> `GuestInfo.ipv4_addresses`에 채운다. `scvmm_mapper.map_scvmm_guest()`도 어댑터 IP를 채운다.
> 그런데 `vm_repo._to_columns()`가 반환하는 15개 키에 IP가 없고, 테이블에도 IP 컬럼이 없다.
> **어댑터가 정상적으로 수집한 값이 저장 직전에 사라진다.**
>
> 의미: Hyper-V 측 IP는 **수집 작업이 아니라 저장·조회 작업**이다. vCenter만 `guest.net` 수집을 추가하면 된다.
> 이 사실이 §12 구현 순서의 1단계를 "저장 경로 먼저"로 정한 근거다.

### 2.3 조회 — 무엇을 줄 수 있는가

| 구간 | 현재 |
|---|---|
| `InventoryQueryService` | `list_virtual_machines(scope, connection_id, page)` 1개 |
| `VirtualMachineRepository.search_vms` | 연결 필터 + 활성 자원 + 정렬 6종 + `COUNT(*)` |
| `Page.ALLOWED_SORT` | name, power_state, guest_os_name, vcpu_count, memory_mb, last_seen_at |
| API | `GET /api/v1/virtual-machines` — `connection_id`, `offset`, `limit`, `sort_by`, `sort_desc` |
| 응답 | `VmSummaryResponse` 12필드 + 중첩 `guest` 7필드 |

`VmSummary`에는 `configured_os`가 채워지지만 **`VmSummaryResponse`가 노출하지 않아 화면에 도달하지 않는다.**

### 2.4 화면 — 무엇을 보여주는가

| 구간 | 현재 |
|---|---|
| `static/index.html` | 7컬럼 표 + 연결 세그먼트 필터 + 페이저. 검색창·정렬 조작·필터 없음 |
| `static/js/resources.js` | 연결 필터·페이징만. URL 동기화 없음. 행 클릭 동작 없음 |
| `static/js/format.js` | `fieldOf()`가 4분기(값/수집불가/해당없음/빈값)를 이미 지원. **`na`(해당 없음)는 아직 아무도 쓰지 않는다** |
| 상세 화면 | 없음 |

### 2.5 정리 — 요건 대비 공백

| 요건 | 공백 |
|---|---|
| FR-402 자원 상세 | 화면·API·DTO 전부 없음 |
| FR-403 통합 검색 | 없음 |
| **FR-404 IP 역조회** | **IP를 저장하지 않음** — 가장 큰 공백 |
| FR-405 다중 조건 필터 | 연결 필터만 |
| FR-406 정렬·페이징 | API만 지원, UI 조작 없음 |
| FR-407 표시 컬럼 선택 | 없음 |
| FR-501 수집 불가 명시 | 게스트 OS·호스트명만. IP·디스크는 대상 자체가 없음 |
| FR-502 신선도 | 클라이언트 24시간 고정 임계값, 연결 단위 판정 없음 |
| FR-1204 4분기 표시 | "해당 없음" 미사용 |

---

## 3. 사용자에게 보여줄 정보 — 확정 카탈로그

**이 절이 이 계획의 계약이다.** §4 이후는 전부 이 표를 채우기 위한 작업이다.

### 3.1 목록 화면 컬럼 (FR-401·413·1203)

**기본 컬럼 9개.** 하이퍼바이저 종류와 무관하게 동일하며, 종류는 배지로만 구분한다 (FR-1203).

| # | 컬럼 | 내용 | 정렬 | 폭 |
|---|---|---|---|---|
| 1 | **이름** | VM 표시 이름 + 부제로 `native_id` | ✔ | 유동 |
| 2 | **종류** | `VC` / `HV` / `VMM` 배지 | ✔ | 76px |
| 3 | **전원** | 실행 중 / 중지 / 일시 중단 / 알 수 없음 | ✔ | 104px |
| 4 | **주 IP** | 대표 IPv4 1건 + 추가 개수(`+2`) | — | 148px |
| 5 | **게스트 OS** | OS 이름 + 구성값이면 `(구성값)` 병기 | ✔ | 200px |
| 6 | **vCPU** | 정수, 우측 정렬 | ✔ | 66px |
| 7 | **메모리** | GiB 환산, 우측 정렬 | ✔ | 100px |
| 8 | **호스트** | 호스트 식별자 (Step 6까지 MoRef/ComputerName) | ✔ | 170px |
| 9 | **수집 시각** | 상대 시각 + 절대 시각 2줄, 신선도 경고 | ✔ | 160px |

**선택 컬럼** (FR-407 — `localStorage` 사용자별 저장). 기본은 숨김.

| 컬럼 | 값이 차는 단계 |
|---|---|
| 연결 이름 | 지금 (연결이 1개면 의미 없어 기본 숨김) |
| 게스트 호스트명(FQDN) | 지금 |
| 클러스터 | **Step 6** |
| 디스크 수 / 총 프로비저닝 용량 | Step 4 |
| 스냅샷 수 / 최신 스냅샷 경과일 | Step 4 |
| NIC 수 | Step 4 |
| HW 버전 / 펌웨어·세대 | Step 4 |
| 부팅 시각 | Step 4 |
| 수명주기 배지 | Step 3 |
| 소유자 / 환경 | **Step 7** |
| BIOS UUID | Step 4 |

> **값이 차지 않는 컬럼은 기본 노출하지 않는다.** 컬럼 정의는 지금 하고(응답 계약 최종 형태 — D-011),
> 화면 기본 노출은 값이 실제로 차는 단계부터 켠다. 빈 컬럼이 늘어난 표는 "수집이 안 되고 있다"로 읽힌다.
> (`docs/01_requirements.md` §8 결정항목 2의 구체화)

**IP 컬럼이 목록에 들어가는 것이 이번 수정의 핵심이다.** IP 없는 VM 목록은 인벤토리가 아니라 이름 목록이다.

### 3.2 상세 화면 항목 (FR-402·412·506·507)

섹션은 계획 11 §16.1을 따른다. 각 항목의 **하이퍼바이저별 출처**를 함께 정의한다 —
이것이 없으면 "왜 이 값만 비었나"에 답할 수 없다 (FR-121·507).

**A. 헤더**

| 항목 | vCenter | 경로 A (호스트) | 경로 B (SCVMM) |
|---|---|---|---|
| VM 이름 | `name` | `Name` | `Name` |
| 전원 상태 | `runtime.powerState` | `State` | `State` |
| 하이퍼바이저 배지 | 연결 유형 | 연결 유형 | 연결 유형 |
| 수명주기 배지 | 포탈 판정 (Step 3) | 동일 | 동일 |
| 신선도 | `last_seen_at` + 연결 상태 | 동일 | 동일 |

**B. 식별** (FR-412)

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 포탈 자원 ID | 포탈 UUID | 동일 | 동일 |
| 하이퍼바이저 고유 ID | `config.instanceUuid` (없으면 MoRef) | VM GUID | VM GUID (`VMId`) |
| BIOS UUID | `config.uuid` | `BiosGuid` | **해당 없음** — SCVMM 미제공 |
| 연결 | 연결 표시명 + 주소 | 동일 | 동일 |
| **수집 경로** | vCenter SOAP | Hyper-V 호스트 직접(JEA) | SCVMM |
| VM 생성일 | `config.createDate` | `CreationTime` | `CreationTime` |

**C. 배치**

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 호스트 | `runtime.host` MoRef → Step 6에서 이름 | `ComputerName` | `HostName` |
| 클러스터 | 호스트 경유 (**Step 6**) | Failover Cluster (**Step 6**) | 호스트 그룹 (**Step 6**) |
| 데이터센터 / 폴더 | 인벤토리 경로 (**Step 6**) | **해당 없음** | **해당 없음** |
| 리소스풀 | `resourcePool` (**Step 6**) | **해당 없음** | **해당 없음** |

**D. 게스트** (FR-501·506) — 이 섹션이 4분기 표기의 시금석이다

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 게스트 정보 상태 | `toolsStatus`·`toolsRunningStatus` 판정 | KVP 유무 판정 | IP·OS 유무 판정 |
| 게스트 OS(감지값) | `guest.guestFullName` | KVP `OSName` | `OperatingSystem` (**VMM DB 캐시** — CST-11) |
| 구성값 OS | `config.guestFullName` | **해당 없음** (CST-12) | `OperatingSystem` |
| OS 출처 배지 | 도구값/구성값 | 항상 도구값 | 구성값 취급 |
| OS 버전 | `guest.guestFamily` | KVP `OSVersion` | — |
| 게스트 호스트명 | `guest.hostName` | KVP `FullyQualifiedDomainName` | — |
| 통합 도구 버전 | `guest.toolsVersion` | 통합 서비스 버전 | — |
| **IPv4 목록** | `guest.net[].ipAddress` | KVP `NetworkAddressIPv4` + 어댑터 | 어댑터 `IPv4Addresses` |
| **IPv6 목록** | 동일 | KVP `NetworkAddressIPv6` | 어댑터 `IPv6Addresses` |
| **마지막 확인 시각** | `guest_observed_at` — 폴백 값이면 원래 관측 시각 | 동일 | 동일 |

**E. 스펙**

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| vCPU 총 수 | `numCPU` | `ProcessorCount` | `ProcessorCount` |
| 소켓 / 소켓당 코어 | `numCoresPerSocket`로 산출 | NUMA 설정 | — |
| 할당 메모리 | `memoryMB` | `MemoryAssigned`(실행 중) / `MemoryStartup` | `MemoryMB` |
| 동적 메모리 범위 | **해당 없음** | `MemoryMinimum`~`MemoryMaximum` | 동적 메모리 설정 |
| HW 버전 / 구성 버전 | `config.version` | `Version` | `Version` |
| 펌웨어 / 세대 | `config.firmware` | Generation 1·2 → BIOS/UEFI | 동일 |
| 부팅 시각 | `runtime.bootTime` | `Uptime`으로 산출 | — |

**F. 스토리지**

디스크 목록 표: `라벨 / 프로비저닝 용량 / 실제 사용량 / 방식 / 데이터스토어 / 파일 경로`
하단에 **총 프로비저닝·총 사용량·디스크 수** 합계.

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 디스크 목록 | `config.hardware.device`의 `VirtualDisk` | `Get-VMHardDiskDrive` + `Get-VHD` | `VirtualHardDisks` |
| 프로비저닝 용량 | `capacityInKB` × 1024 | VHD `Size` | `MaximumSize` |
| 실제 사용량 | **1차 구현에서 미수집** (계획 04 §6.5) | VHD `FileSize` | `Size` |
| 방식 | `backing.thinProvisioned` | 동적 확장 / 고정 | 동일 |
| 데이터스토어·경로 | `backing.fileName` 파싱 | VHD `Path` | `Location` |

> 실제 사용량이 vCenter에서 비는 것은 **결함이 아니라 알려진 한계**다 (계획 04 §6.5).
> 화면은 `—`가 아니라 **"수집 불가 — 확인 필요"** 로 표기해 다른 셀과 구분한다.

**G. 네트워크**

어댑터 목록 표: `MAC / 어댑터 타입 / 연결 네트워크 / 연결 상태 / IP 주소`

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| MAC | 장치 `macAddress` | `Get-VMNetworkAdapter` `MacAddress` | 어댑터 `MACAddress` |
| 어댑터 타입 | 장치 클래스 → `vmxnet3` 등 | 합성/레거시 | 동일 |
| 연결 네트워크 | 포트그룹명 (분산은 키→Step 6 해석) | 가상 스위치명 | 논리 네트워크/스위치 |
| 연결 상태 | `connectable.connected` | `Status` | `Enabled` |

**H. 스냅샷** (FR-803 연계)

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 개수·목록 | `snapshot` 트리 | `Get-VMSnapshot` | `Get-SCVMCheckpoint` |
| 이름·생성일·경과일 | `createTime` | `CreationTime` | 동일 |
| 점유 용량 | delta 파일 크기 (**미수집**) | AVHDX 크기 | — |
| 원본 용어 병기 | 스냅샷 | **체크포인트** | **체크포인트** |

**I. 하이퍼바이저 메타**

| 항목 | vCenter | 경로 A | 경로 B |
|---|---|---|---|
| 노트/주석 | `config.annotation` | `Notes` | `Description` |
| Custom Attributes | `customValue` (**Step 8** — FR-606) | **해당 없음** | **해당 없음** |

**J. 포탈 메타데이터** — 자리만 만든다 (**Step 7**). 소유자·팀·용도·환경·중요도·수명주기·태그.
**K. 변경 이력** — 자리만 만든다 (**Step 7**).

### 3.3 표기 규칙 — 4분기 (FR-501·1204·508, NFR-408)

**서버가 판정하고 서버가 문구를 만든다.** 화면·내보내기·API가 각자 판정하면 같은 데이터가 셋으로 갈린다.

| 상태 | 표기 | 스타일 | 발생 예 |
|---|---|---|---|
| 수집된 값 | `10.0.0.5` | 값 | 정상 |
| **수집 불가** | `⚠ 수집 불가 — 게스트 도구 미설치`<br>부제 `마지막 확인: 10.0.0.5 (3일 전)` | warning | Tools/KVP 미동작 |
| **해당 없음** | `◌ 해당 없음` | muted | Hyper-V의 리소스풀, SCVMM의 BIOS UUID |
| 빈 값 | `—` | muted | NIC 0개, 스냅샷 0개 |

`static/js/format.js`의 `fieldOf()`가 이미 4분기를 구현하고 있다. **이번 수정에서 `notSupported`(해당 없음)
경로가 처음으로 실사용된다** — 서버가 능력 정보를 내려보내야 한다 (FR-309).

### 3.4 IP 표기 세칙

| 상황 | 목록 | 상세 |
|---|---|---|
| IPv4 1건 | `10.0.0.5` | 전체 목록 |
| IPv4 여러 건 | `10.0.0.5` + `+2` 칩 | 어댑터별로 묶어 전부 |
| IPv4 없고 IPv6만 | `(IPv6만)` + 툴팁 | IPv6 목록 |
| 게스트 도구 미동작 | `⚠ 수집 불가 — …` | 사유 + 마지막 확인 값·시각 |
| 도구 정상, IP 없음 | `—` | `—` |

**링크로컬(169.254./fe80::)·루프백은 저장 단계에서 제외한다** (ROADMAP §19 완료 기준).
`src/utils/net.py`의 `split_ip_families()`가 이미 그 역할을 하는지 확인하고, 없으면 필터를 추가한다.

---

## 4. 수집 확장

### 4.1 vCenter (`vcenter/property_specs.py`)

```python
VM_PROPERTIES: list[str] = [
    *VM_PROPERTIES_MVP,                 # 기존 13개 유지
    "config.version",                   # HW 버전 (vmx-19)
    "config.firmware",                  # bios | efi
    "config.hardware.numCoresPerSocket",
    "config.hardware.device",           # 디스크 · NIC  ← 응답 크기의 대부분
    "config.annotation",
    "config.createDate",
    "runtime.bootTime",
    "guest.net",                        # IP 목록
    "guest.toolsVersion",
    "snapshot",                         # 스냅샷 트리
]
```

> **`config.hardware.device`가 수집 시간을 늘린다** (ROADMAP §19 주의).
> Step 2에서 측정한 값과 **같은 환경에서** 전후를 비교해 증가분을 `docs/04_field_validation.md`에 기록한다.
> 증가분이 허용 범위를 넘으면 장치 수집을 별도 PropertyCollector 호출로 분리하는 안을 검토한다.

매핑은 계획 04 §6.3(장치)·§6.5(Thin 사용량)를 그대로 구현한다.
`guest.net[]`은 `ipAddress` 배열과 `macAddress`를 함께 주므로, **어댑터의 MAC과 대조해 IP를 어댑터에 귀속**시킨다.
대조 실패한 IP는 어댑터 미상으로 VM에 직접 붙인다 — 버리지 않는다.

### 4.2 Hyper-V 경로 A (`hyperv/host_scripts.py`)

추가 수집: `Get-VMHardDiskDrive` + `Get-VHD`, `Get-VMSnapshot`, 어댑터의 `MacAddress`·`SwitchName`·`Status`,
`MemoryMinimum`/`MemoryMaximum`, `CreationTime`.

> **⚠ 스크립트를 수정하면 JEA 역할 파일을 반드시 재생성한다.**
> ```bash
> python scripts/generate_jea_role.py
> ```
> `scripts/jea/WzonePortalReadOnly.psrc`의 `VisibleCmdlets`에 위 cmdlet이 추가되어야 하며,
> 어긋나면 `tests/integration/test_ps_scripts_live.py`가 실패한다 (CLAUDE.md).
> 새 cmdlet은 전부 `Get-*`다 — **쓰기 cmdlet이 역할 파일에 들어가면 NFR-201·202 위반**이다.

목 cmdlet(`tests/ps_mocks/hyperv_cmdlet_mocks.ps1`)에도 새 cmdlet을 추가해야 테스트가 관통한다.

### 4.3 Hyper-V 경로 B (`hyperv/scvmm_scripts.py`)

추가 수집: `VirtualHardDisks`(`MaximumSize`·`Size`·`Location`), `Get-SCVMCheckpoint`,
어댑터 `MACAddress`·`VirtualNetwork`, `DynamicMemoryEnabled`·최소/최대, `CreationTime`.

목 fabric(`tests/ps_mocks/scvmm_fabric.ps1`)의 8개 시나리오에 새 필드를 추가한다.
**쓰기 cmdlet 트랩은 그대로 유지**한다.

### 4.4 수집 속성 대칭성 점검

| 속성 | vCenter | 경로 A | 경로 B | 비대칭 처리 |
|---|---|---|---|---|
| BIOS UUID | ✔ | ✔ | ✖ | 경로 B는 **해당 없음** |
| 구성값 OS | ✔ | ✖ | ✔ | 경로 A는 **해당 없음** (CST-12) |
| 디스크 실제 사용량 | ✖(1차) | ✔ | ✔ | vCenter는 **수집 불가** |
| 스냅샷 점유 용량 | ✖ | ✔ | ✖ | 미수집은 **수집 불가** |
| 리소스풀 | Step 6 | ✖ | ✖ | Hyper-V는 **해당 없음** |

**"해당 없음"과 "수집 불가"를 어댑터가 구분해 보고한다** (FR-309). 이것을 어댑터 밖에서 판단하면
`if hypervisor == ...` 분기가 유스케이스로 새어 나온다 (arch_check 위반).

---

## 5. DB 스키마 (마이그레이션 `0003`)

계획 06 §2.3·2.4·2.5·2.9의 해당 부분을 그대로 적용한다. **축소하지 않는다** (D-011).

### 5.1 `virtual_machines` 컬럼 추가

```sql
ALTER TABLE virtual_machines
    ADD COLUMN socket_count            INTEGER,
    ADD COLUMN cores_per_socket        INTEGER,
    ADD COLUMN dynamic_memory          BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN dynamic_min_mb          BIGINT,
    ADD COLUMN dynamic_max_mb          BIGINT,
    ADD COLUMN hw_version              TEXT,
    ADD COLUMN firmware                TEXT,
    ADD COLUMN generation              INTEGER,
    ADD COLUMN guest_os_version        TEXT,
    ADD COLUMN guest_tool_version      TEXT,
    ADD COLUMN boot_time               TIMESTAMPTZ,
    ADD COLUMN cluster_native_id       TEXT,          -- Step 6에서 채운다
    ADD COLUMN annotation              TEXT,
    ADD COLUMN created_at_hv           TIMESTAMPTZ,
    -- 집계 (목록 성능 — 원본은 하위 테이블, NFR-108)
    ADD COLUMN total_provisioned_bytes BIGINT,
    ADD COLUMN total_used_bytes        BIGINT,
    ADD COLUMN disk_count              INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN adapter_count           INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN snapshot_count          INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN latest_snapshot_at      TIMESTAMPTZ,
    ADD COLUMN snapshot_size_bytes     BIGINT;
```

`missing_since`·`power_state_changed_at`은 Step 3의 몫이므로 여기서 만들지 않는다 —
이미 있으면 중복 추가하지 않도록 마이그레이션에서 확인한다.

### 5.2 하위 컬렉션 테이블 신설

계획 06 §2.4를 그대로 쓴다 (`vm_disks`, `vm_adapters`, `vm_adapter_ips`).
스냅샷은 계획 06 §2.5의 `snapshots` 테이블(연결 단위 자원, `vm_native_id`로 VM 참조)을 쓴다.

```sql
CREATE TABLE vm_disks (...);          -- 계획 06 §2.4 그대로
CREATE TABLE vm_adapters (...);       -- 계획 06 §2.4 그대로
CREATE TABLE vm_adapter_ips (...);    -- 계획 06 §2.4 그대로
CREATE TABLE snapshots (...);         -- 계획 06 §2.5 그대로
```

**`vm_adapter_ips`에 `resource_id`를 중복 저장한다.** IP 역조회에서 어댑터 조인 없이 VM에 바로 도달하기
위함이며, 최다 사용 시나리오의 응답 시간이 정규화보다 우선이다 (계획 06 §2.4).

### 5.3 인덱스

계획 06 §2.9 중 이 단계에 해당하는 것만 만든다.

```sql
CREATE INDEX idx_adapter_ips_addr     ON vm_adapter_ips (ip_address);
CREATE INDEX idx_adapter_ips_resource ON vm_adapter_ips (resource_id);
CREATE INDEX idx_adapters_mac         ON vm_adapters (mac_address);
CREATE INDEX idx_vm_name_trgm         ON virtual_machines USING gin (name gin_trgm_ops);
CREATE INDEX idx_vm_hostname_trgm     ON virtual_machines USING gin (guest_hostname gin_trgm_ops);
CREATE INDEX idx_vm_power_active      ON virtual_machines (power_state) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_host              ON virtual_machines (host_native_id) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_os                ON virtual_machines (guest_os_name) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_guest_avail       ON virtual_machines (guest_availability) WHERE lifecycle = 'active';
CREATE INDEX idx_vm_last_seen         ON virtual_machines (last_seen_at);
CREATE INDEX idx_vm_snapshot_age      ON virtual_machines (latest_snapshot_at)
    WHERE snapshot_count > 0 AND lifecycle = 'active';
```

`pg_trgm` 확장은 마이그레이션 `0001`에서 이미 생성했다 (ROADMAP §7). **여기서 처음 실사용된다** —
실배포 DB에서 확장이 실제로 설치되었는지 Step 2에서 확인한 결과를 전제로 한다 (ROADMAP §15.4).

---

## 6. 도메인 모델

### 6.1 값 객체 추가 (`src/domain/values.py`)

계획 02 §5.2를 따른다.

```python
@dataclass(frozen=True, slots=True)
class VirtualDisk:
    key: str
    label: str | None = None
    provisioned_bytes: int = 0
    used_bytes: int | None = None                    # None = 수집 불가 (빈 값 아님)
    provisioning: DiskProvisioning = DiskProvisioning.UNKNOWN
    datastore_name: str | None = None
    file_path: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkAdapter:
    key: str
    mac_address: str | None = None                   # 정규화 형식 (소문자 콜론)
    adapter_type: str | None = None
    network_name: str | None = None
    connected: bool | None = None
    ipv4_addresses: tuple[str, ...] = ()
    ipv6_addresses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotInfo:
    native_id: str
    name: str
    created_at: datetime | None = None
    size_bytes: int | None = None
    is_current: bool = False
```

`MemorySpec`에 `dynamic_enabled`·`dynamic_min_mb`·`dynamic_max_mb`,
`CpuSpec`에 이미 있는 `socket_count`·`cores_per_socket`을 실제로 채운다.

### 6.2 `VirtualMachine` 확장 (`src/domain/resource.py`)

```python
disks: tuple[VirtualDisk, ...] = ()
adapters: tuple[NetworkAdapter, ...] = ()
snapshots: tuple[SnapshotInfo, ...] = ()
annotation: str | None = None
boot_time: datetime | None = None
created_at_hv: datetime | None = None
cluster_native_id: str | None = None

@property
def mac_addresses(self) -> tuple[str, ...]:
    return tuple(a.mac_address for a in self.adapters if a.mac_address)
```

**`mac_addresses`가 실제 값을 반환하기 시작하면 CI 식별 3순위(MAC+이름)가 자동으로 살아난다.**
`build_vm_identity_keys()`는 이미 이 프로퍼티를 쓰도록 작성되어 있다 (계획 02 §7).
BIOS UUID(2순위)도 함께 켠다 — 이것이 D-011이 "식별 구조를 축소하지 않는다"고 한 이유다.

> **⚠ 식별 키를 늘리면 기존 레코드와의 매칭 결과가 바뀔 수 있다.** 2·3순위를 켠 첫 수집에서
> 기존 자원과 신규 자원의 매칭이 어떻게 달라지는지 **스테이징에서 먼저 확인**한다 (§14).

### 6.3 능력 정보 (FR-309)

계획 03 §3의 `ReaderCapabilities`를 도입한다. Step 3에서 이미 도입되었으면 속성 단위로 확장한다.

```python
@dataclass(frozen=True, slots=True)
class ReaderCapabilities:
    resource_types: frozenset[ResourceType]
    #: 이 경로가 원리적으로 제공할 수 없는 속성. "해당 없음" 표기의 근거가 된다.
    unsupported_fields: frozenset[str] = frozenset()
```

| 경로 | `unsupported_fields` |
|---|---|
| vCenter | `{"dynamic_memory", "generation"}` |
| 경로 A | `{"configured_os", "resource_pool", "datacenter_path"}` |
| 경로 B | `{"bios_uuid", "resource_pool", "datacenter_path", "guest_tool_version"}` |

### 6.4 조회 모델 (`src/domain/query.py`)

`VmSummary`에 필드를 추가하고, `VmDetail`을 신설한다 (계획 07 §2).

```python
@dataclass(frozen=True, slots=True)
class VmSummary:
    ...                                    # 기존 필드 유지
    primary_ipv4: str | None
    ipv4_count: int
    cluster_native_id: str | None
    disk_count: int
    total_provisioned_bytes: int | None
    snapshot_count: int
    latest_snapshot_at: datetime | None
    hw_version: str | None
    is_stale: bool                         # FR-502·508 — 서버 판정
    stale_reason: StaleReason | None


@dataclass(frozen=True, slots=True)
class VmDetail:
    vm: VirtualMachine
    connection: ConnectionSummary          # 표시명·유형·주소 (자격증명 없음)
    collection_path: CollectionPath        # FR-507
    capabilities: ReaderCapabilities       # FR-309 — "해당 없음" 판정 근거
    quality: DataQuality
    # metadata / host / cluster / recent_changes 는 Step 6·7에서 채운다
```

`Page.ALLOWED_SORT`에 추가: `host_native_id`, `cluster_native_id`, `disk_count`,
`total_provisioned_bytes`, `snapshot_count`, `latest_snapshot_at`, `first_seen_at`.

**IP는 정렬 컬럼에 넣지 않는다** — LATERAL 서브쿼리 결과 정렬은 인덱스를 타지 못한다.

### 6.5 검색 조건 (`SearchCriteria`)

계획 07 §3.3을 그대로 도입한다. `lifecycles` 기본값 `{ACTIVE}`를 반드시 유지한다 —
미발견·폐기 자원이 기본 목록에 섞이면 운영자가 없는 VM을 실재한다고 오인한다.

---

## 7. 저장소 (`infrastructure/repository/vm_repo.py`)

### 7.1 Upsert에 하위 컬렉션 추가

```python
async def upsert_virtual_machines(self, connection_id, vms, observed_at) -> UpsertResult:
    for vm in vms:
        ...                                              # 기존 식별·본체 갱신
        await self._sync_disks(resource_id, vm.disks)
        await self._sync_adapters(resource_id, vm.adapters)   # IP 포함
        await self._sync_snapshots(connection_id, vm)
        await self._update_aggregates(resource_id, vm)        # 집계 컬럼
```

**하위 컬렉션은 `DELETE` 후 `INSERT`로 동기화한다.** 디스크·어댑터는 장치 키가 재사용될 수 있고
개별 diff의 이득이 없다. `_sync_identities()`가 이미 같은 방식이다.

**집계 컬럼은 저장 시점에 계산한다.** 조회 때 `SUM`을 걸면 목록 5,000건에서 무너진다 (NFR-108).

```python
def _aggregates(vm: VirtualMachine) -> dict[str, object]:
    return {
        "disk_count": len(vm.disks),
        "adapter_count": len(vm.adapters),
        "total_provisioned_bytes": sum(d.provisioned_bytes for d in vm.disks) or None,
        # used_bytes가 하나라도 None이면 합계도 None — 부분 합은 오해를 부른다
        "total_used_bytes": (
            sum(d.used_bytes for d in vm.disks)
            if vm.disks and all(d.used_bytes is not None for d in vm.disks) else None
        ),
        "snapshot_count": len(vm.snapshots),
        "latest_snapshot_at": max((s.created_at for s in vm.snapshots if s.created_at), default=None),
        "snapshot_size_bytes": (
            sum(s.size_bytes for s in vm.snapshots)
            if vm.snapshots and all(s.size_bytes is not None for s in vm.snapshots) else None
        ),
    }
```

`_TRACKED_COLUMNS`에 새 컬럼을 추가한다 — 추가하지 않으면 Step 7의 변경 이력이 그 속성을 놓친다.

### 7.2 게스트 IP 폴백

`GuestInfo.with_fallback()`이 이미 `ipv4_addresses`·`ipv6_addresses`를 폴백 대상에 포함한다.
**어댑터 테이블도 같은 규칙을 따라야 한다** — 도구가 멈췄다고 어댑터 IP 행을 지우면 폴백이 무의미해진다.

```python
async def _sync_adapters(self, resource_id, adapters, guest_collected: bool) -> None:
    """게스트 도구 미동작이면 IP 행을 지우지 않는다 (ROADMAP §7.4와 같은 원칙).

    어댑터 자체(MAC·타입·스위치)는 하이퍼바이저 구성 정보라 도구와 무관하게 갱신한다.
    IP만 게스트 의존이므로 IP 행의 삭제를 조건부로 한다.
    """
```

### 7.3 조회 쿼리

계획 07 §3.2의 목록 SQL을 도입한다. 지금과 달라지는 것:

- `primary_ip` LATERAL 서브쿼리 추가
- `SearchCriteria` 필터 절 추가 (모든 파라미터는 `NULL`이면 무시)
- `ORDER BY {sort} , vm.resource_id` 타이브레이커 **유지**
- 총 건수 근사 전환 (계획 07 §7.2) — `COUNT_EXACT_THRESHOLD = 10_000`

검색 3종을 추가한다 (계획 07 §4).

```python
async def find_vms_by_ip(self, scope, ip, include_inactive) -> list[VmSummary]: ...
async def find_vms_by_mac(self, scope, mac, page) -> PagedResult[VmSummary]: ...
async def find_vms_by_ip_prefix(self, scope, cidr, page) -> PagedResult[VmSummary]: ...
async def search_vms_by_text(self, scope, kw, page) -> PagedResult[VmSummary]: ...
```

상세 조회는 **본체 1회 + 하위 컬렉션 3회**로 끝낸다. 어댑터별 IP를 N+1로 조회하지 않는다.

> **`_apply_scope()`를 새 쿼리 5개 전부에 적용한다.** 계획 09 §10이 "이 계획의 가장 흔한 결함"으로
> 지목한 것이 바로 이 누락이다. §13 완료 기준에 검증 항목을 두었다.

---

## 8. 애플리케이션 (`application/inventory_query.py`)

```python
class InventoryQueryService:
    async def list_virtual_machines(
        self, scope: AccessScope, criteria: SearchCriteria, page: Page
    ) -> PagedResult[VmSummary]:
        scope.require(Permission.RESOURCE_READ)
        page.validate()
        criteria.validate()
        return await self._repo.search_vms(scope, criteria, page)

    async def search(
        self, scope: AccessScope, keyword: str, criteria: SearchCriteria, page: Page
    ) -> SearchResult:
        """입력 형태를 판별해 경로를 나눈다 (FR-410, 계획 07 §4.2).

        판별 결과를 응답에 실어 UI가 "IP 역조회 결과 2건"처럼 맥락을 표시하게 한다.
        """

    async def get_virtual_machine(self, scope: AccessScope, resource_id: UUID) -> VmDetail:
        """범위 밖 자원은 404다. 403은 그 자원이 존재한다는 사실을 알려준다."""
```

**시그니처가 `connection_id`에서 `criteria`로 바뀐다.** 호출부는 API 라우터 1곳뿐이라 파급이 작다.
지금 바꾸지 않으면 필터가 늘 때마다 인자가 늘어난다.

`connection_id` 단독 필터는 `SearchCriteria(connection_ids={...})`로 표현한다 —
**범위 밖 connection_id는 403이 아니라 빈 결과**라는 기존 규칙을 그대로 유지한다 (ROADMAP §9).

---

## 9. API

### 9.1 엔드포인트

| 메서드 | 경로 | 권한 | 변경 |
|---|---|---|---|
| GET | `/api/v1/virtual-machines` | 인증 | **파라미터 확장** |
| GET | `/api/v1/virtual-machines/{resource_id}` | 인증 | **신설** (FR-402) |

**검색은 별도 엔드포인트를 만들지 않고 목록의 `q` 파라미터로 통합한다.** 별도로 만들면 검색 결과에
필터를 조합할 수 없고, URL 동기화(FR-411)도 두 벌이 된다.

### 9.2 목록 파라미터

| 파라미터 | 타입 | 비고 |
|---|---|---|
| `q` | str | 검색어. IP/MAC/IP접두/텍스트 자동 판별 (FR-410) |
| `connection_id` | UUID[] | 다중 허용 |
| `hypervisor` | enum[] | `vcenter` \| `hyperv` |
| `power_state` | enum[] | |
| `guest_availability` | enum[] | 도구 미설치 목록 조회 (FR-504) |
| `guest_os` | str | 부분 일치, 2자 이상 |
| `host` / `cluster` | str[] | native_id |
| `lifecycle` | enum[] | 기본 `active` |
| `has_snapshot` | bool | 스냅샷 보유 (FR-803 연계) |
| `stale_before` | datetime | 신선도 초과 (FR-502) |
| `offset` / `limit` / `sort_by` / `sort_desc` | | 기존 유지 |

**배열 파라미터는 상한을 둔다** (연결 50개, 호스트 200개). 상한 없는 `IN` 절은 느려진다.

### 9.3 응답 계약

`VmSummaryResponse`에 추가:

```python
network: NetworkSummaryResponse       # primary_ipv4, ipv4_count, adapter_count
storage: StorageSummaryResponse       # disk_count, total_provisioned_bytes, total_used_bytes
snapshot: SnapshotSummaryResponse     # count, latest_at, size_bytes
freshness: FreshnessResponse          # is_stale, stale_reason, last_seen_at  (FR-508)
configured_os: str | None             # 지금 채워지는데 노출되지 않던 값
cluster_native_id: str | None
hw_version: str | None
lifecycle: ResourceLifecycle          # 기존
```

`GuestInfoResponse`에 `ipv4_addresses`·`ipv6_addresses`를 추가한다 —
**주석으로 예약해 둔 자리를 이번에 채운다** (`api/schemas/inventory.py`).

목록 응답 래퍼에 검색 맥락을 싣는다.

```python
class VmListResponse(PagedResponse[VmSummaryResponse]):
    #: q가 있을 때만. UI가 "IP 역조회 결과"처럼 맥락을 표시한다 (FR-410)
    search_kind: SearchKind | None = None
```

상세 응답 `VmDetailResponse`는 §3.2의 섹션 구조를 그대로 반영하되,
**각 필드가 "해당 없음"인지 서버가 판정해 내려보낸다** (FR-309·NFR-408).

```python
class FieldStateResponse(BaseModel):
    """4분기를 서버가 판정한 결과. UI는 분기 없이 그대로 출력한다."""
    state: Literal["value", "unavailable", "not_supported", "empty"]
    value: str | None = None
    reason: str | None = None          # unavailable일 때 사유 문구
    last_value: str | None = None      # 폴백 값 (FR-506)
    observed_at: datetime | None = None
```

> **모든 필드를 `FieldStateResponse`로 감싸지 않는다.** 수집 불가·미지원이 발생할 수 있는 필드
> (게스트 파생값, 디스크 사용량, 리소스풀, BIOS UUID 등)에만 적용한다. `name`·`vcpu_count`처럼
> 항상 값이 있는 필드까지 감싸면 응답이 3배가 되고 화면 코드가 장황해진다.

---

## 10. UI

### 10.1 목록 화면 (`static/index.html`, `js/resources.js`)

| 항목 | 작업 |
|---|---|
| 컬럼 | 7 → 9개. `components.css`의 `.cols-vm` grid-template-columns 갱신 |
| 검색 바 | 상단에 단일 입력. 엔터 시 `q` 파라미터. 판별 결과를 칩으로 표시 (`IP 역조회`) |
| 필터 | 연결·하이퍼바이저·전원·게스트 상태·수명주기. 적용된 필터를 칩으로 표시하고 개별 해제 |
| 정렬 | 표 헤더 클릭 → `sort_by`/`sort_desc` 토글, 방향 아이콘 |
| URL 동기화 | 모든 조회 상태를 `location.search`에 반영. 뒤로가기·새로고침·링크 공유 지원 (FR-411) |
| 행 클릭 | 상세 화면으로 이동. **행 전체가 링크** (읽기 전용이므로 행 클릭에 다른 동작이 없다) |
| 빈 결과 | 4가지 원인 구분 (FR-414) — 연결 없음 / 범위 없음 / 수집 전 / 필터 결과 없음 |
| 범례 | `해당 없음` 항목 추가 (`state--na`) |
| 총 건수 | `total_is_estimate`면 `약 12,000건` |

정렬·필터 변경 시 **offset을 0으로 초기화**한다 (계획 11 §14.5).

### 10.2 상세 화면 (`static/vm.html`, `js/vm.js` — 신설)

§3.2의 A~K 섹션 순서를 그대로 구현한다.

- **조작 버튼을 넣지 않는다** (FR-1206). 전원·스냅샷·삭제·마이그레이션 어느 것도 없다.
  상세 화면은 조작 UI가 가장 자연스럽게 스며드는 자리다 — 리뷰 시 이 항목을 명시적으로 확인한다
- 원본 용어 병기 (FR-1214): `스냅샷 3개 (Hyper-V 체크포인트)`
- OS 출처 배지 (FR-304): `Windows Server 2019 ⓘ VM 구성값 — 실제와 다를 수 있음`
- Step 6·7 섹션(소속 링크·메타데이터·이력)은 **자리를 만들고 "다음 단계에서 제공" 안내**를 둔다.
  섹션 자체를 나중에 끼워 넣으면 레이아웃을 다시 잡게 된다
- 화면 시안은 계획 11 §16과 같은 Claude Design 캔버스에 이어서 만든다 (ROADMAP §10.3)

### 10.3 `format.js` 확장

```javascript
function ipField(vm)        // 주 IP + 추가 개수, 수집 불가 4분기
function fmtBytes(n)        // GiB/TiB 환산
function snapshotAgeField(latestAt, count)   // 경과일 + 오래된 스냅샷 경고
function freshnessField(freshness)           // FR-508 — 서버 판정을 그대로 표시
```

**`fieldOf()`의 `notSupported` 경로를 실제로 사용한다.** 서버가 `state: "not_supported"`를 주면
`◌ 해당 없음`으로 렌더한다.

---

## 11. 성능

| 항목 | 목표 | 확인 방법 |
|---|---|---|
| IP 역조회 | 5,000건 기준 **1초 이내** (NFR-101) | `EXPLAIN (ANALYZE, BUFFERS)`에서 `Index Scan on idx_adapter_ips_addr` |
| 이름 검색 | 2초 이내 (NFR-102) | `Bitmap Index Scan on idx_vm_name_trgm` |
| 목록 조회 | 1초 이내 (50건) | LATERAL 서브쿼리가 행당 1회만 실행되는지 |
| 총 건수 | 10,000 초과 시 근사 전환 | `total_is_estimate=true` |
| **수집 시간 증가** | Step 2 실측 대비 증가분 기록 | `config.hardware.device` 추가 전후 비교 |

`pg_trgm`의 `%` 연산자 임계값은 기본 0.3이다. 짧은 검색어에서 결과가 안 나오면
`pg_trgm.similarity_threshold`를 낮춘다 (계획 07 §4.2).

목록·검색 결과는 **캐시하지 않는다** — 조회 범위가 사용자마다 달라 캐시 키가 폭발한다 (계획 07 §7.3).

---

## 12. 구현 순서

**저장 경로를 먼저 만든다.** Hyper-V는 이미 IP를 수집하고 있으므로(§2.2), 저장만 열면
vCenter 수집 확장 전에 **Hyper-V 자원으로 IP 역조회를 실증**할 수 있다.

| # | 작업 | 검증 |
|---|---|---|
| 1 | 마이그레이션 `0003` — 컬럼·테이블·인덱스 | `upgrade`/`downgrade` 왕복. `alembic.ini`는 **ASCII만** (Known Mistakes) |
| 2 | 도메인 값 객체·`VirtualMachine` 확장 | 단위 테스트. `mac_addresses`가 어댑터에서 나오는지 |
| 3 | `vm_repo` 하위 컬렉션 upsert + 집계 | **2회 수집 → 중복 0건**, 집계값 일치 |
| 4 | **Hyper-V 경로로 IP 저장 확인** | fake/목 fabric 수집 → `vm_adapter_ips` 행 생성 |
| 5 | `find_vms_by_ip` + API `q` | `EXPLAIN` Index Scan, 복수 결과, 범위 필터 |
| 6 | vCenter `guest.net`·`device`·`snapshot` 수집 | vcsim으로 관통. **수집 시간 전후 비교 기록** |
| 7 | 경로 A·B 디스크·스냅샷·MAC 수집 | JEA 역할 재생성, 목 cmdlet 갱신, PS 테스트 통과 |
| 8 | CI 식별 2·3순위 활성화 | 기존 자원 매칭이 바뀌지 않는지 스테이징 확인 |
| 9 | `SearchCriteria` 다중 필터 | 조합 정확도, NULL 파라미터 무시 |
| 10 | 검색 4경로 분기 | IP / MAC / IP접두 / 텍스트 |
| 11 | 상세 API + `VmDetail` | 범위 밖 자원 404, N+1 없음 |
| 12 | 목록 화면 개선 (컬럼·필터·정렬·URL) | 뒤로가기·링크 공유 |
| 13 | 상세 화면 | **조작 버튼 0개** 확인 |
| 14 | 총 건수 근사 전환 | 상한 초과 시 `약 N건` |
| 15 | `arch_check.py` · 전체 테스트 | 하이퍼바이저 분기 없음 |

---

## 13. 완료 기준

- [ ] **IP 주소로 검색해 1초 이내에 해당 VM에 도달** (NFR-101). 링크로컬·루프백이 결과에 없음
- [ ] 같은 IP를 가진 자원이 복수일 때 전부 표시되고 `lifecycle`로 구분됨
- [ ] MAC / IP 접두(`10.0.1.`) / 이름 / 호스트명 검색이 각각 동작
- [ ] 목록 9개 기본 컬럼이 vCenter·Hyper-V 자원에 **동일 포맷**으로 표시됨 (FR-1203)
- [ ] 상세 화면에 §3.2 A~I 섹션이 표시되고 J·K는 자리 표시
- [ ] **4분기 표기가 전부 실사용됨** — 값 / 수집 불가 / **해당 없음** / 빈 값
- [ ] SCVMM 자원의 BIOS UUID가 `해당 없음`, 경로 A 자원의 구성값 OS가 `해당 없음`으로 표시됨
- [ ] 게스트 도구 미동작 VM의 IP가 `수집 불가 — … (마지막 확인: …)`로 표시되고 **값이 보존됨**
- [ ] 재수집 후에도 `vm_adapter_ips` 행이 도구 미동작으로 사라지지 않음
- [ ] 2회 수집 시 VM·디스크·어댑터·스냅샷 어디에도 중복 레코드 없음
- [ ] **새 조회 메서드 5개 전부가 `AccessScope`를 받고 SQL에 반영** (계획 09 §10)
- [ ] 범위 밖 자원이 목록·검색·상세 어디에도 노출되지 않음. 상세는 404
- [ ] 정렬 컬럼이 화이트리스트로 제한되고, 페이지 경계에서 행 중복·누락 없음
- [ ] 필터·검색·정렬·페이지가 URL에 반영되고 링크 공유가 동작
- [ ] 상세 화면에 자원을 변경하는 UI 요소가 **하나도 없음** (FR-1206)
- [ ] `config.hardware.device` 추가에 따른 수집 시간 증가분이 기록됨
- [ ] JEA 역할 파일이 재생성되어 PS 테스트 통과. 역할에 **쓰기 cmdlet 없음**
- [ ] `arch_check.py` 통과 — 어댑터 직접 import 없음, 유스케이스에 하이퍼바이저 분기 없음

---

## 14. 선행 조건과 순서 리스크

| 항목 | 내용 |
|---|---|
| **Step 2 (실환경 실측)** | 속성 경로가 vSphere 6.5에 존재하는지 확인되지 않은 상태에서 `config.version`·`config.createDate`·`runtime.bootTime`을 추가하면 `missingSet`으로 떨어진다. 코드는 `None`으로 견디지만 **화면이 전부 빈 값이 되어 원인 파악이 어렵다** |
| **Step 3 (다중 연결·자동 수집)** | FR-508의 연결 단위 신선도 판정은 `connections.status`와 수집 이력이 있어야 성립한다. Step 3 이전이면 자원 단위 판정만 구현하고 연결 단위는 자리만 만든다 |
| **Step 6 (Host/Cluster)** | 호스트·클러스터 **이름**은 Step 6에서 해석된다. 이 계획은 식별자를 그대로 표시하고, 컬럼 자리만 확보한다 |
| **Hyper-V 실환경** | 경로 A·B는 코드와 목 테스트만 완료된 상태다 (D-018). 디스크·스냅샷 수집 추가는 **목 cmdlet 기준으로 구현**하고, 실환경 검증은 SCVMM·JEA 준비 후로 미룬다 |

**Step 2·3보다 먼저 착수해야 한다면** 4·5번(Hyper-V IP 저장 + IP 역조회)만 떼어 선행할 수 있다.
이 두 작업은 vCenter 속성 추가와 무관하고, 목 fabric으로 완결 검증이 가능하다.

---

## 15. 주의사항

- **범위 필터 누락이 가장 위험한 결함이다.** 새 조회 경로 5개마다 `scope` 인자와 SQL 반영을 확인한다.
  범위 없는 전체 조회 함수를 편의로 만들지 않는다 (계획 09 §10)
- **집계값을 조회 시점에 계산하지 않는다.** 목록에서 `SUM(disk.provisioned_bytes)`를 걸면
  5,000건에서 무너진다. 저장 시점 계산이 원칙이다 (NFR-108)
- **부분 합계를 만들지 않는다.** 디스크 사용량이 하나라도 미수집이면 합계는 `None`이다.
  일부만 더한 값을 총량처럼 보여주면 용량 판단이 틀어진다
- **어댑터 IP 행을 게스트 도구 미동작으로 삭제하지 않는다.** 폴백 원칙이 본체 컬럼에만 적용되고
  하위 테이블에서 깨지면 상세 화면의 IP만 사라진다
- **`ORDER BY` 타이브레이커를 빠뜨리지 않는다.** 정렬 컬럼에 동값이 많으면 페이지 경계에서 행이 샌다
- **정렬 컬럼을 사용자 입력 그대로 SQL에 넣지 않는다.** `ALLOWED_SORT` 화이트리스트를 유지한다
- **`config.hardware.device`는 응답 크기의 대부분을 차지한다.** 수집 시간 증가를 측정 없이 넘기지 않는다
- **`host_scripts.py`·`scvmm_scripts.py` 수정 시 JEA 역할과 목 cmdlet을 함께 갱신한다.**
  새로 추가하는 cmdlet은 전부 `Get-*`여야 한다
- **상세 화면에 조작 버튼이 들어오기 쉽다.** 시안 단계와 구현 후 두 번 확인한다 (계획 11 §6.1)
- **CI 식별 2·3순위를 켜는 것은 되돌리기 어렵다.** 스테이징에서 매칭 변화를 먼저 확인한다

---

## 16. 결정이 필요한 항목

| # | 항목 | 기본 제안 | 영향 |
|---|---|---|---|
| 1 | 목록 기본 컬럼 9개가 적정한가 — 연결·클러스터·소유자를 기본에 넣을지 | **선택 컬럼**으로 두고 값이 차는 단계에 기본 전환 | §3.1 |
| 2 | 검색을 목록 `q`로 통합할지 별도 화면으로 둘지 | **목록 통합** (필터 조합·URL 동기화가 한 벌) | §9.1, 계획 11 §15 |
| 3 | 디스크 파일 경로를 화면에 노출할지 | **노출** (인벤토리 가치 있음). 단 내보내기 감사 대상 (NFR-206·211) | §3.2 F |
| 4 | vCenter Thin 디스크 실제 사용량 확보 방법 | 1차는 미수집(`수집 불가` 표기). 필요 시 별도 조사 | 계획 04 §6.5 |
| 5 | 오래된 스냅샷 경고 기준일 | **7일** | §10.3, FR-803 |
| 6 | 표시 컬럼 선택(FR-407) 저장 위치 — `localStorage` / 서버 | **`localStorage`** (사용자 설정 테이블을 지금 만들지 않는다) | §10.1 |
| 7 | 배열 파라미터 상한 | 연결 50 / 호스트 200 | §9.2 |

---

## 17. 참조

| 문서 | 절 |
|---|---|
| `docs/01_requirements.md` | §3.5·3.6 (보완 요건 FR-410~414, FR-506~508), §5 (NFR-108·408) |
| `plans/ROADMAP.md` | §19 (Step 4 범위·완료 기준), §9.2 (응답 계약) |
| `plans/02-domain-model.md` | §5.2 (값 객체), §7 (CI 식별 2·3순위), §9.3 (IP·MAC 정규화) |
| `plans/04-vcenter-adapter.md` | §5 (속성 목록), §6.3 (장치 매핑), §6.5 (Thin 사용량) |
| `plans/05-hyperv-adapter.md` | §8.2~8.6 (경로별 매핑) |
| `plans/06-collection-scheduler.md` | §2.3·2.4·2.5 (DDL), §2.9 (인덱스), §3.3 (게스트 병합) |
| `plans/07-inventory-query.md` | §2 (조회 모델), §3.2 (목록 SQL), §4 (검색), §7 (성능) |
| `plans/08-api-server.md` | §6.2·6.3 (조회 API·응답 스키마) |
| `plans/11-web-ui.md` | §14 (목록), §15 (검색), §16 (상세) |
| `docs/03_design_system.md` | §2 (상태 표현 규칙) |
