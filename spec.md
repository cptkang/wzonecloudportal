# 클라우드 포탈 요건 정의서

> 버전: 0.2
> 작성일: 2026-08-06
> 성격: **읽기 전용 통합 자원 인벤토리 포탈** (CMDB 성격)
> `[TODO]` 항목은 고객 환경 고유 정보로, 사용자 확정이 필요합니다. requirements-analyst는 이를 임의로 채우지 말고 "미확정"으로 표시하세요.

---

## 1. 프로젝트 개요

### 1.1 목적

조직 내에 분산된 **다수의 VMware vCenter 인스턴스와 다수의 Hyper-V 호스트/클러스터**에 존재하는 가상자원 정보를
자동으로 수집·정규화하여, **단일 포탈에서 통합 조회·검색·리포팅**할 수 있는 자원 인벤토리 시스템을 구축한다.

관리자가 vCenter마다, Hyper-V 관리 콘솔마다 따로 접속하여 자원 현황을 확인하고 수작업으로 취합하는 문제를 해소하고,
IP·스펙·OS 등 자원 정보의 **단일 진실 공급원(Single Source of Truth)** 을 확보하는 것이 목표다.

### 1.2 범위

**포함**
- 다수 vCenter / Hyper-V 인스턴스 연결 및 인벤토리 자동 수집
- 하이퍼바이저별 상이한 자원 모델의 공통 모델 정규화
- 자원 정보(식별 정보, 네트워크, 스펙, OS, 스토리지, 소속 관계) 통합 조회·검색
- 조직 고유 메타데이터(소유자, 용도, 환경 등) 부여 및 관리
- 자원 변경 이력 추적, 신규/삭제 자원 감지
- 현황 대시보드, 리포트 내보내기, 외부 연동용 조회 API

**제외 (명시적 비목표)**
- **자원 생성·변경·삭제 기능 일체** (VM 프로비저닝, 전원 제어, 스냅샷 조작, 리소스 변경, 마이그레이션)
- 성능 모니터링 및 실시간 알람 (별도 모니터링 시스템의 영역)
- 백업·재해복구 관리
- 하이퍼바이저 설정 변경

> **핵심 안전 원칙**: 포탈은 하이퍼바이저에 대해 **어떠한 쓰기 API도 호출하지 않는다.**
> 접속 계정 또한 읽기 전용 권한만 부여받는다. 이는 요건이자 아키텍처 제약이다.

### 1.3 핵심 워크플로우

```
하이퍼바이저 연결 등록 (vCenter A/B/C…, Hyper-V 호스트·클러스터 …)
    ↓  (읽기 전용 자격증명, 암호화 저장)
수집 스케줄러가 연결별로 주기적 인벤토리 수집
    ↓  (vCenter: PropertyCollector / Hyper-V: WinRM+WMI·KVP)
하이퍼바이저 고유 모델 → 공통 자원 모델로 정규화
    ↓
CI 식별 규칙으로 기존 자원과 대조 → 신규/변경/삭제 판정
    ↓
인벤토리 저장소 갱신 + 변경 이력 기록
    ↓
포탈 사용자가 통합 조회·검색·리포트 (저장소 기반, 하이퍼바이저 직접 호출 없음)
```

### 1.4 기술 스택

| 구분 | 기술 | 비고 |
|------|------|------|
| API 서버 | FastAPI + uvicorn | 웹 UI + REST API |
| vCenter 수집 | pyVmomi (PropertyCollector) | 대규모 환경 대응 위해 ContainerView + RetrievePropertiesEx 페이징 사용 |
| Hyper-V 수집 | pypsrp / WinRM (PowerShell Remoting), WMI `root\virtualization\v2` | 게스트 정보는 KVP 통합 서비스 경유 |
| DB | PostgreSQL + SQLAlchemy (async) | 인벤토리, 메타데이터, 변경 이력, 감사 로그 |
| 캐시 | Redis | 조회 캐시, 수집 작업 큐 |
| 설정 | pydantic-settings | 타입 안전 환경변수 관리 |
| 인증·인가 | JWT + RBAC | 조회 범위 제어 |
| 테스트 | pytest + pytest-asyncio | 어댑터 계약 테스트 포함 |

---

## 2. 관리 대상 자원 및 속성 카탈로그

수집 항목은 vSphere 인벤토리 리포팅의 사실상 표준인 **RVTools**의 속성 집합(vInfo/vCPU/vMemory/vDisk/vNetwork/vHost/vDatastore)과
ITIL CMDB의 구성 항목(CI) 속성 개념을 기준으로 정의한다.

### 2.1 자원 유형 및 하이퍼바이저 대응

| 공통 자원 | vCenter | Hyper-V | 정규화 비고 |
|---|---|---|---|
| **VirtualMachine** | VirtualMachine | VM (`Msvm_ComputerSystem`) | 공통 |
| **Host** | HostSystem (ESXi) | Hyper-V 호스트 (Windows Server) | 공통 |
| **Cluster** | ClusterComputeResource | Failover Cluster | 개념 대응. 클러스터 미구성 단독 호스트 처리 규칙 필요 |
| **Datastore** | Datastore (VMFS/NFS/vSAN) | CSV / SMB 공유 / 로컬 볼륨 | 개념 대응. 용량 산정 기준 통일 필요 |
| **Network** | 표준 포트그룹 / 분산 포트그룹 | 가상 스위치 (External/Internal/Private) | 개념 대응. VLAN 표현 방식 상이 |
| **Datacenter / Folder** | Datacenter, Folder 경로 | 없음 | Hyper-V는 논리 그룹 부재 → 포탈 메타데이터로 대체 |
| **ResourcePool** | ResourcePool | 없음 | Hyper-V 미지원 → 조회 시 N/A 표기 |
| **Snapshot** | Snapshot | 체크포인트 (Checkpoint) | 명칭만 상이, 정보 구조 유사 |

### 2.2 VirtualMachine 속성 (핵심 자원)

| 분류 | 속성 | vCenter 출처 | Hyper-V 출처 | 필수 |
|---|---|---|---|---|
| **식별** | 포탈 자원 ID (UUID) | 포탈 생성 | 포탈 생성 | ✔ |
| | 하이퍼바이저 고유 ID | `instanceUuid`, MoRef | VM GUID (`Msvm_ComputerSystem.Name`) | ✔ |
| | BIOS UUID | `config.uuid` | `Msvm_VirtualSystemSettingData.BIOSGUID` | ✔ |
| | VM 표시 이름 | `name` | `ElementName` | ✔ |
| **소속** | 연결(하이퍼바이저 인스턴스)명 | 포탈 연결 정보 | 포탈 연결 정보 | ✔ |
| | 데이터센터 / 폴더 경로 | 인벤토리 경로 | N/A | |
| | 클러스터 | ClusterComputeResource | Failover Cluster 이름 | ✔ |
| | 호스트 | `runtime.host` | 소유 노드 | ✔ |
| | 리소스풀 | `resourcePool` | N/A | |
| **상태** | 전원 상태 | `runtime.powerState` | `EnabledState` | ✔ |
| | 연결 상태 | `runtime.connectionState` | 하트비트 상태 | ✔ |
| | 가동 시작 시각 | `runtime.bootTime` | 가동 시간 | |
| **스펙(CPU)** | vCPU 총 수 | `config.hardware.numCPU` | `Msvm_ProcessorSettingData.VirtualQuantity` | ✔ |
| | 소켓 수 / 소켓당 코어 | `numCPU`, `numCoresPerSocket` | NUMA 설정 | ✔ |
| **스펙(메모리)** | 할당 메모리 (MB) | `config.hardware.memoryMB` | `Msvm_MemorySettingData.VirtualQuantity` | ✔ |
| | 동적 메모리 여부·범위 | N/A | 동적 메모리 최소/최대 | |
| **스펙(플랫폼)** | VM 하드웨어 버전 | `config.version` | VM 구성 버전 | ✔ |
| | 펌웨어 (BIOS/UEFI) | `config.firmware` | 1세대/2세대 | ✔ |
| **OS** | 게스트 OS (구성값) | `config.guestFullName` | 구성값 | ✔ |
| | 게스트 OS (실제 감지값) | `guest.guestFullName` | KVP `OSName` | ✔ |
| | OS 버전 / 빌드 | `guest.guestFamily` | KVP `OSVersion` | |
| | 게스트 호스트명 (FQDN) | `guest.hostName` | KVP `FullyQualifiedDomainName` | ✔ |
| | 통합 도구 상태·버전 | VMware Tools 상태/버전 | 통합 서비스(Integration Services) 버전 | ✔ |
| **네트워크** | NIC 목록 (어댑터 타입) | `config.hardware.device` | `Msvm_SyntheticEthernetPort` | ✔ |
| | MAC 주소 | 장치 `macAddress` | `PermanentAddress` | ✔ |
| | 연결 네트워크 / 스위치 | 포트그룹명 | 가상 스위치명 | ✔ |
| | **IPv4 주소 목록** | `guest.net[].ipAddress` | KVP `NetworkAddressIPv4` | ✔ |
| | IPv6 주소 목록 | `guest.net[].ipAddress` | KVP `NetworkAddressIPv6` | |
| | NIC 연결 상태 | `connected` | 연결 상태 | |
| **스토리지** | 가상 디스크 목록 | `config.hardware.device` (VirtualDisk) | `Msvm_StorageAllocationSettingData` | ✔ |
| | 디스크별 프로비저닝 용량 | `capacityInKB` | VHD/VHDX 최대 크기 | ✔ |
| | 디스크별 실제 사용량 | 데이터스토어 사용량 | VHDX 파일 크기 | ✔ |
| | 프로비저닝 방식 (Thin/Thick) | `backing.thinProvisioned` | 동적 확장 / 고정 | ✔ |
| | 디스크 파일 경로 / 데이터스토어 | `backing.fileName` | VHD 경로 | ✔ |
| | VM 총 프로비저닝 / 사용 용량 | 집계 | 집계 | ✔ |
| **스냅샷** | 스냅샷 개수 | 스냅샷 트리 | 체크포인트 목록 | ✔ |
| | 최신 스냅샷 생성일 | `createTime` | 생성일 | ✔ |
| | 스냅샷 총 점유 용량 | delta 파일 크기 | AVHDX 크기 | |
| **하이퍼바이저 메타** | 노트/주석 | `config.annotation` | Notes | |
| | 태그 / 사용자 정의 속성 | vSphere Tags, Custom Attributes | N/A | |
| | VM 생성일 | `config.createDate` | 생성일 | |
| **수집 메타** | 최초 발견 시각 / 최종 수집 시각 | 포탈 생성 | 포탈 생성 | ✔ |
| | 수집 상태 (완전/부분/실패) | 포탈 판정 | 포탈 판정 | ✔ |
| **포탈 부여 메타** | 소유자 / 담당팀 | 포탈 입력 | 포탈 입력 | ✔ |
| | 용도 / 서비스명 | 포탈 입력 | 포탈 입력 | |
| | 환경 구분 (운영/개발/테스트) | 포탈 입력 | 포탈 입력 | ✔ |
| | 중요도 (Tier) | 포탈 입력 | 포탈 입력 | |
| | 수명주기 상태 (사용중/유휴/폐기예정) | 포탈 입력·판정 | 포탈 입력·판정 | ✔ |

> **중요 제약**: 게스트 OS 정보와 IP 주소는 **VMware Tools(vCenter) 또는 통합 서비스·KVP(Hyper-V)가 설치·동작 중일 때만 수집 가능**하다.
> 미설치 VM은 IP·게스트 호스트명·실제 OS를 알 수 없으며, 포탈은 이를 "수집 불가"로 명시적으로 구분하여 표시해야 한다 (FR-501 참조).
> 특히 Linux 게스트는 IP 할당 방식(DHCP/고정) 정보가 제공되지 않는 경우가 있다.

### 2.3 Host 속성

| 분류 | 속성 |
|---|---|
| 식별 | 호스트명, FQDN, 관리 IP, 하이퍼바이저 고유 ID |
| 상태 | 연결 상태, 유지보수 모드, 가동 시간 |
| 하드웨어 | 제조사, 모델, **시리얼 번호(서비스 태그)**, CPU 모델·소켓 수·코어 수·클럭, 총 물리 메모리 |
| 소프트웨어 | 하이퍼바이저 종류(ESXi/Hyper-V), 버전, 빌드 번호 |
| 소속 | 소속 클러스터, 소속 연결(vCenter/Hyper-V) |
| 집계 | 등록 VM 수, 전원 켜진 VM 수, vCPU 할당 합계, 메모리 할당 합계, 오버커밋 비율 |
| 네트워크 | 물리 NIC 목록(속도, 연결 상태), 가상 스위치 목록 |

### 2.4 Cluster 속성

이름, 소속 연결, 호스트 수, VM 수, 총 CPU 코어·클럭, 총 메모리, 할당 합계, 오버커밋 비율,
고가용성 설정(vCenter: HA/DRS, Hyper-V: Failover Cluster 쿼럼 구성), `[TODO]` 클러스터 미구성 단독 호스트의 표현 방식

### 2.5 Datastore 속성

이름, 유형(VMFS/NFS/vSAN/CSV/SMB/로컬), 총 용량, 사용 용량, 여유 용량,
**프로비저닝 용량(오버커밋 판단용)**, 연결된 호스트 목록, 배치된 VM 수, 경로/URL

### 2.6 Network 속성

이름, 유형(표준 포트그룹/분산 포트그룹/가상 스위치), VLAN ID, 연결된 호스트, 연결된 VM 수, 업링크 정보

---

## 3. 기능 요건

### 3.1 하이퍼바이저 연결 관리 (FR-1xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-101 | 연결 등록 | vCenter / Hyper-V 호스트·클러스터를 연결로 등록. 주소, 포트, 자격증명, 표시명, 설명 입력 | Must |
| FR-102 | 다중 연결 지원 | **다수의 vCenter와 다수의 Hyper-V 인스턴스를 동시에 등록·관리** | Must |
| FR-103 | 연결 테스트 | 등록 시 접속 가능 여부, 권한 충분 여부를 사전 검증 | Must |
| FR-104 | 자격증명 관리 | 접속 정보 암호화 저장, 비밀번호 교체, 조회 시 마스킹 | Must |
| FR-105 | 연결 활성/비활성 | 점검 중인 연결을 수집 대상에서 일시 제외 | Must |
| FR-106 | 연결 상태 표시 | 마지막 수집 성공 시각, 최근 오류 메시지, 연속 실패 횟수 | Must |
| FR-107 | 인증서 검증 정책 | 연결별 TLS 인증서 검증 여부 설정 (자체 서명 인증서 대응) | Must |

### 3.2 인벤토리 수집·동기화 (FR-2xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-201 | 스케줄 수집 | 연결별 주기 설정에 따라 자동 수집 | Must |
| FR-202 | 수동 수집 | 관리자가 특정 연결의 즉시 수집을 실행 | Must |
| FR-203 | 대규모 환경 대응 수집 | vCenter는 PropertyCollector + ContainerView로 필요한 속성만 일괄 조회하고, 결과 토큰으로 페이징 처리 | Must |
| FR-204 | 부분 실패 허용 | 일부 연결 장애 시 나머지 연결의 수집은 정상 완료. 실패 연결은 마지막 성공 데이터를 유지하며 "오래된 데이터"로 표시 | Must |
| FR-205 | 수집 이력 | 수집 작업별 시작·종료 시각, 소요 시간, 대상 자원 수, 신규/변경/삭제 건수, 오류 내역 기록 | Must |
| FR-206 | 수집 부하 제어 | 하이퍼바이저 API 호출 타임아웃, 동시 연결 수 상한, 재시도 상한 적용 | Must |
| FR-207 | 증분 갱신 | 변경된 자원만 갱신하여 수집 시간·DB 부하 절감 | Should |
| FR-208 | 수집 항목 선택 | 연결별로 수집할 자원 유형 선택 (예: 특정 vCenter는 VM만) | Could |

### 3.3 자원 정규화 및 데이터 정합성 (FR-3xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-301 | 모델 정규화 | 하이퍼바이저 고유 모델을 §2의 공통 자원 모델로 변환 | Must |
| FR-302 | **CI 식별 규칙** | 자원을 고유 식별하는 속성 우선순위를 정의하여 재수집 시 동일 자원으로 인식. VM 기준: ① 연결 ID + 하이퍼바이저 고유 ID → ② BIOS UUID → ③ MAC 주소 + 이름 | Must |
| FR-303 | 중복 자원 방지 | 동일 자원이 중복 레코드로 생성되지 않도록 보장. 갱신 시 기존 레코드를 수정 | Must |
| FR-304 | **속성 출처 우선순위(조정 규칙)** | 동일 속성을 여러 경로로 얻는 경우 신뢰 순위를 정의. 예: 게스트 OS는 도구 감지값 > 구성값. 포탈 입력 메타데이터는 수집 데이터가 덮어쓰지 않음 | Must |
| FR-305 | VM 이동 추적 | vMotion·라이브 마이그레이션으로 호스트가 바뀌어도 동일 자원으로 유지하고 이동 이력을 기록 | Must |
| FR-306 | 자원 간 관계 유지 | VM ↔ Host ↔ Cluster ↔ Datastore ↔ Network 관계를 저장하고 양방향 조회 지원 | Must |
| FR-307 | 삭제 자원 처리 | 수집 결과에서 사라진 자원을 즉시 삭제하지 않고 "미발견" 상태로 유예 후 폐기 처리 (유예 기간 설정 가능) | Must |
| FR-308 | 연결 간 중복 감지 | 동일 자원이 서로 다른 연결에서 중복 수집되는 경우 감지·경고 | Should |

### 3.4 통합 조회·검색 (FR-4xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-401 | 통합 자원 목록 | 하이퍼바이저 종류·연결과 무관하게 동일한 포맷으로 자원 목록 조회 | Must |
| FR-402 | 자원 상세 | §2 속성 카탈로그의 전체 정보를 상세 화면에서 조회 | Must |
| FR-403 | 통합 검색 | 이름, IP, 호스트명, MAC, OS, 소유자 등 주요 속성 대상 검색 | Must |
| FR-404 | **IP 주소 역조회** | IP를 입력하면 해당 IP를 보유한 VM을 즉시 찾는다 (장애 대응 시 최다 사용 시나리오) | Must |
| FR-405 | 다중 조건 필터 | 연결, 하이퍼바이저 종류, 클러스터, 호스트, 전원 상태, OS, 환경, 소유자, 태그 조합 필터 | Must |
| FR-406 | 정렬·페이징 | 대량 자원 목록의 컬럼 정렬 및 페이징 | Must |
| FR-407 | 표시 컬럼 선택 | 사용자가 목록에 표시할 속성 컬럼을 선택하고 저장 | Should |
| FR-408 | 저장된 조회 조건 | 자주 쓰는 필터 조합을 저장·재사용 | Could |
| FR-409 | 관계 탐색 | 상세 화면에서 소속 호스트·클러스터·데이터스토어·네트워크로 이동 | Must |

### 3.5 데이터 품질 관리 (FR-5xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-501 | **수집 불가 항목 명시** | VMware Tools·통합 서비스 미설치 등으로 IP·OS를 얻지 못한 경우 "없음"이 아니라 **"수집 불가(사유)"** 로 구분 표시 | Must |
| FR-502 | 데이터 신선도 표시 | 자원별 최종 수집 시각과 기준 시간 초과 시 경고 표시 | Must |
| FR-503 | 필수 메타데이터 누락 목록 | 소유자·환경 등 포탈 부여 메타데이터가 비어 있는 자원 목록 제공 | Must |
| FR-504 | 도구 미설치 자원 목록 | VMware Tools / 통합 서비스 미설치·구버전 VM 목록 제공 | Must |
| FR-505 | 데이터 품질 지표 | 전체 자원 대비 정보 완전성 비율 대시보드 표시 | Should |

### 3.6 메타데이터 관리 (FR-6xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-601 | 자원 메타데이터 입력 | 소유자, 담당팀, 용도, 환경, 중요도, 수명주기 상태를 포탈에서 부여·수정 | Must |
| FR-602 | 메타데이터 보존 | 재수집 시 포탈 입력값이 유지되고 수집 데이터에 덮어쓰이지 않음 | Must |
| FR-603 | 태그 관리 | 자유 태그 부여·검색 | Should |
| FR-604 | 일괄 편집 | 여러 자원을 선택해 메타데이터 일괄 부여 | Should |
| FR-605 | 일괄 가져오기 | Excel/CSV로 메타데이터 일괄 등록 | Should |
| FR-606 | 하이퍼바이저 태그 활용 | vSphere Tags·Custom Attributes·Notes를 수집하여 메타데이터 초기값으로 활용 | Could |

### 3.7 변경 이력 및 수명주기 (FR-7xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-701 | 속성 변경 이력 | 수집 결과 비교로 자원 속성 변경을 감지하고 `언제 / 어떤 속성 / 이전값 → 새값` 이력 기록 | Must |
| FR-702 | 추적 대상 속성 | 최소한 IP, 호스트명, vCPU, 메모리, 디스크 용량, 전원 상태, 소속 호스트·클러스터, OS를 추적 | Must |
| FR-703 | 신규 자원 감지 | 신규 발견 자원 목록 제공 (기간별) | Must |
| FR-704 | 삭제 자원 감지 | 미발견 전환 자원 목록 제공 | Must |
| FR-705 | 자원 이력 타임라인 | 자원 상세에서 변경 이력을 시간순으로 조회 | Must |
| FR-706 | 변경 이력 보존 기간 | `[TODO]` 이력 보존 기간 및 아카이브 정책 | Must |

### 3.8 리포트 및 내보내기 (FR-8xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-801 | 인벤토리 내보내기 | 현재 조회 조건의 자원 목록을 Excel/CSV로 내보내기 | Must |
| FR-802 | 자원 현황 리포트 | 연결별·클러스터별·환경별 VM 수, vCPU·메모리·스토리지 할당 집계 | Must |
| FR-803 | 스냅샷 현황 리포트 | 오래된 스냅샷 보유 VM 목록 (기준일 초과), 스냅샷 점유 용량 | Must |
| FR-804 | 유휴 자원 리포트 | 장기간 전원 꺼짐 상태인 VM 등 정리 후보 목록 | Should |
| FR-805 | 용량 현황 리포트 | 데이터스토어 사용률, 프로비저닝 오버커밋 비율 | Should |
| FR-806 | OS 분포 리포트 | 게스트 OS별 자원 수 집계 (지원 종료 OS 파악용) | Should |
| FR-807 | 정기 리포트 발송 | 지정 주기로 리포트를 메일 발송 | Could |

> FR-803·FR-804는 조사 과정에서 확인된 가상화 환경의 대표적 낭비 요인(오래된 스냅샷, 좀비 VM)에 대응한다.
> **포탈은 정리 대상을 식별해 보고할 뿐, 삭제·정리 작업은 수행하지 않는다** (§1.2 비목표).

### 3.9 대시보드 (FR-9xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-901 | 전체 현황 요약 | 총 연결 수, 호스트 수, VM 수, 전원 상태 분포 | Must |
| FR-902 | 자원 할당 현황 | 총 vCPU·메모리·스토리지 할당량과 물리 용량 대비 비율 | Must |
| FR-903 | 하이퍼바이저별 분포 | vCenter/Hyper-V별, 연결별 자원 분포 | Must |
| FR-904 | 수집 상태 위젯 | 연결별 마지막 수집 시각, 실패 연결 강조 | Must |
| FR-905 | 최근 변경 위젯 | 최근 신규/삭제/변경 자원 요약 | Should |

### 3.10 인증·권한 및 감사 (FR-10xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-1001 | 사용자 인증 | 포탈 로그인 (JWT) | Must |
| FR-1002 | 역할 기반 권한 | 최소 3역할: 조회자(자원 조회), 운영자(메타데이터 편집), 관리자(연결·사용자 관리) | Must |
| FR-1003 | 조회 범위 제한 | 사용자·그룹별로 조회 가능한 연결 또는 자원 그룹을 제한 | Must |
| FR-1004 | 감사 로그 | 로그인, 연결 등록·수정, 자격증명 변경, 메타데이터 변경, 리포트 내보내기 기록 | Must |
| FR-1005 | 외부 인증 연동 | `[TODO]` AD / LDAP / SSO 연동 필요 여부 | TBD |

### 3.11 외부 연동 API (FR-11xx)

| ID | 기능 | 설명 | 우선순위 |
|---|---|---|---|
| FR-1101 | 조회 REST API | 자원 목록·상세를 조회하는 인증된 REST API 제공 | Must |
| FR-1102 | 검색 API | IP·이름 기준 조회 API (타 시스템의 자산 조회 연동용) | Must |
| FR-1103 | 변경 이력 API | 기간별 신규/변경/삭제 자원 조회 API (외부 CMDB 동기화용) | Should |
| FR-1104 | API 인증·제한 | API 키 또는 토큰 인증, 호출량 제한 | Must |

> **API는 조회 전용이다.** 자원을 변경하는 엔드포인트는 제공하지 않는다.

---

## 4. 비기능 요건

### 4.1 성능·확장성

| ID | 요건 |
|---|---|
| NFR-101 | 자원 목록 조회 응답: 저장소 기반으로 1초 이내 (1만 건 기준, 페이징) |
| NFR-102 | 검색 응답: 2초 이내 |
| NFR-103 | 수집 소요 시간: `[TODO]` 관리 대상 규모 확정 후 목표 설정. vCenter 1대 기준 목표치 필요 |
| NFR-104 | `[TODO]` 관리 대상 규모: vCenter 수, Hyper-V 호스트·클러스터 수, 총 VM 수 |
| NFR-105 | `[TODO]` 수집 주기 및 허용 데이터 지연 시간 |
| NFR-106 | 하이퍼바이저 종류 추가(예: Proxmox, Nutanix, KVM) 시 어댑터 구현만으로 지원 가능한 구조 |
| NFR-107 | 수집은 API 서버와 독립적으로 동작하여 수집 부하가 조회 응답에 영향을 주지 않음 |

### 4.2 보안

| ID | 요건 |
|---|---|
| NFR-201 | **하이퍼바이저 접속 계정은 읽기 전용 권한만 사용** (vCenter: Read-Only 역할, Hyper-V: 읽기 권한 계정) |
| NFR-202 | **포탈 코드는 하이퍼바이저의 쓰기·제어 API를 호출하지 않는다.** 어댑터에 조회 메서드만 정의한다 |
| NFR-203 | 자격증명은 암호화 저장하며 로그·API 응답·예외 메시지·객체 표현에 노출 금지 |
| NFR-204 | 모든 조회는 사용자 권한 범위로 필터링 |
| NFR-205 | 관리 작업(연결 등록·수정, 자격증명 변경)은 감사 로그 필수 |
| NFR-206 | 인벤토리 정보(IP·호스트명·OS) 자체가 민감 정보이므로 내보내기 기능도 감사 대상 |
| NFR-207 | `[TODO]` 하이퍼바이저 접속용 서비스 계정의 최소 권한 범위 확정 |

### 4.3 안정성·운영

| ID | 요건 |
|---|---|
| NFR-301 | 일부 하이퍼바이저 장애 시에도 나머지 자원 조회는 정상 동작 |
| NFR-302 | 수집 실패 시 기존 데이터를 삭제하지 않고 유지하며 신선도를 표시 |
| NFR-303 | 수집 작업 중단·재시작 시 데이터 정합성 유지 |
| NFR-304 | 하이퍼바이저 호출 타임아웃 기본 60초, 재시도 최대 3회 |
| NFR-305 | 수집 실패 연속 발생 시 관리자 알림 `[TODO]` 알림 수단(메일/메신저) |

### 4.4 사용성

| ID | 요건 |
|---|---|
| NFR-401 | 자원 목록은 하이퍼바이저 종류와 무관하게 동일한 화면·용어로 표시 |
| NFR-402 | 하이퍼바이저 고유 용어는 공통 용어로 통일하되, 상세 화면에서 원본 용어 병기 |
| NFR-403 | 한국어 UI |
| NFR-404 | `[TODO]` 지원 브라우저 범위 |

---

## 5. 제약 조건

| ID | 제약 |
|---|---|
| CST-01 | **자원 생성·변경·삭제 기능을 구현하지 않는다.** 향후 요구가 생기면 별도 프로젝트로 분리한다 |
| CST-02 | 게스트 OS·IP 정보는 VMware Tools / Hyper-V 통합 서비스에 의존하므로 100% 수집을 보장할 수 없다 |
| CST-03 | Hyper-V는 vCenter와 같은 중앙 관리 지점이 없으므로, SCVMM 부재 시 호스트·클러스터 단위로 개별 연결해야 한다 |
| CST-04 | 개발·테스트는 목(mock) 커넥터를 사용하며 운영 하이퍼바이저에 연결하지 않는다 |
| CST-05 | `[TODO]` 네트워크 제약: 포탈 서버 → vCenter(443) / Hyper-V(WinRM 5985·5986) 방화벽 허용 필요 |
| CST-06 | `[TODO]` 배포 환경 (OS, 컨테이너 여부, 폐쇄망 여부) |
| CST-07 | `[TODO]` Hyper-V 환경의 SCVMM 도입 여부 — 도입 시 수집 방식이 달라짐 |

---

## 6. Phase 계획

| Phase | 범위 | 완료 기준 |
|---|---|---|
| **Phase 1** | 연결 관리, vCenter/Hyper-V 어댑터, 정규화, 수집 스케줄러, VM/Host 인벤토리 저장·조회 API | 다수 vCenter·Hyper-V에서 VM 목록과 §2.2 필수 속성이 통합 조회됨 |
| **Phase 2** | 통합 검색·필터, IP 역조회, 자원 상세, 관계 탐색, 데이터 품질 표시 | IP로 VM을 1초 내 찾을 수 있음 |
| **Phase 3** | 메타데이터 관리, 변경 이력·수명주기, 인증·권한·감사 | 소유자 정보가 재수집에도 보존되고 속성 변경 이력이 남음 |
| **Phase 4** | 대시보드, 리포트·내보내기, 외부 연동 API | 스냅샷·유휴 자원 리포트와 Excel 내보내기 동작 |

`[TODO]` Phase 우선순위 조정 및 일정 확정

---

## 7. 조사 근거

이 요건 정의서는 아래 자료 조사를 근거로 작성했습니다.

- CMDB 구성 항목(CI)·속성 정의 및 중복 방지 원칙 — [Atlassian: What Is CMDB](https://www.atlassian.com/itsm/it-asset-management/cmdb), [Cloudaware: CMDB CI Explained](https://cloudaware.com/blog/cmdb-ci/), [ITIL CMDB 가이드](https://cloudaware.com/blog/itil-cmdb/)
- CI 식별 규칙·조정 규칙(다중 수집 소스의 중복·충돌 처리) — [ServiceNow: CMDB Identification & Reconciliation](https://www.servicenow.com/community/cmdb-articles/cmdb-identification-reconciliation/ta-p/2301712)
- vSphere 인벤토리 표준 속성 집합(vInfo/vCPU/vMemory/vDisk/vNetwork/vHost/vDatastore) — [RVTools 4 개요](https://4sysops.com/archives/whats-new-in-rvtools-4-for-vmware-vsphere/), [RVTools 데이터 분석](https://sizing-workshop.readthedocs.io/en/latest/datacollection/rvtools/rvtools.html)
- 대규모 환경의 효율적 수집(PropertyCollector·ContainerView·RetrievePropertiesEx 페이징) — [Broadcom: Using the PropertyCollector with RetrievePropertiesEx](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere-sdks-tools/8-0/web-services-sdk-programming-guide/property-collector/using-the-propertycollector-with-retrievepropertiesex.html), [VMware: Efficient Data Retrieval with PropertyCollector and ContainerView](https://blogs.vmware.com/cloud-foundation/2024/10/21/efficient-data-retrieval-with-vi-json-api-propertycollector-and-containerview/)
- pyVmomi 인벤토리 객체 수집 범위 — [pyVmomi tutorial: core vCenter inventory objects](https://vthinkbeyondvm.com/pyvmomi-tutorial-how-to-get-all-the-core-vcenter-server-inventory-objects-and-play-around/)
- VMware Tools 미설치 시 게스트 IP·OS 수집 제약 — [Broadcom KB: VMware Tools 상태 이슈](https://knowledge.broadcom.com/external/article/343269/vsphere-client-reports-the-status-of-vmw.html), [open-vm-tools: Linux 게스트 IP origin 미설정](https://github.com/vmware/open-vm-tools/issues/694)
- Hyper-V 게스트 정보 수집(KVP: OSName·OSVersion·FQDN·NetworkAddressIPv4) 및 WMI 조회 — [Microsoft Learn: Get-VMNetworkAdapter](https://learn.microsoft.com/en-us/powershell/module/hyper-v/get-vmnetworkadapter?view=windowsserver2025-ps), [Microsoft Learn: Hyper-V WMI 네트워크 어댑터 조회](https://learn.microsoft.com/en-us/answers/questions/135604/hyper-v-fetch-network-adapter-related-information(), [Retrieving the IP Address Of A VM In Hyper-V](https://learn.microsoft.com/hu-hu/archive/blogs/taylorb/retrieving-the-ip-address-of-a-vm-in-hyper-v)
- Hyper-V 다중 호스트·클러스터 관리 방식(SCVMM 유무에 따른 차이) — [TechTarget: SCVMM vs Hyper-V Manager](https://www.techtarget.com/searchitoperations/tip/SCVMM-vs-Hyper-V-Manager-Which-tasks-are-best-suited-to-each), [Veeam: SCVMM, Cluster, or Standalone](https://community.veeam.com/blogs-and-podcasts-57/scvmm-cluster-or-standalone-what-changes-when-you-pick-each-one-13215)
- 다중 vCenter 통합 조회 개념(Enhanced Linked Mode) — [O'Reilly: Managing inventory via vCenter](https://www.oreilly.com/library/view/vmware-vsphere-5-5/9781784398750/ch02s09.html)
- 좀비 VM·고아 VMDK·오래된 스냅샷 식별 및 용량 회수 리포팅 — [Broadcom KB: Zombie/Orphaned disks 식별](https://knowledge.broadcom.com/external/article/383876/how-to-identify-zombie-orphaned-disks-i.html), [SolarWinds: VM Sprawl Control](https://www.solarwinds.com/virtualization-manager/use-cases/vm-sprawl-control), [Zombie VM 정의](https://www.ituonline.com/tech-definitions/what-is-a-zombie-vm/)
- 구성 드리프트 감지·변경 이력·감사 추적 요건 — [Microsoft Learn: Azure Change Tracking and Inventory](https://learn.microsoft.com/en-us/azure/azure-change-tracking-inventory/overview-monitoring-agent), [Open-AudIT: Configuration Management & Change Detection](https://open-audit.com/network-configuration-management/)
