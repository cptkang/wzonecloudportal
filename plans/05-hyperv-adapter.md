# 05. Hyper-V 수집 어댑터

> Wave: 2
> 계층: infrastructure (`src/infrastructure/hyperv/`)
> 담당 요건: FR-103(인증 방식), FR-106, FR-301, `spec.md` §2.2 Hyper-V 출처 열, CST-03·06
> 의존: 02, 03
> 관련 결정: D-003, D-005, D-008

## 1. 목적

WinRM(PowerShell Remoting) / WMI로 Hyper-V 인벤토리를 수집하여 공통 도메인 모델로 반환한다.
`HypervisorInventoryReader` Protocol(계획 03)을 구현한다.

**04(vCenter 어댑터)를 절대 import하지 않는다.** arch-check 특화 규칙 7번 위반이다.

## 2. vCenter와의 근본적 차이 (설계 전제)

`docs/00_research_notes.md` §7.2에 따르면 Hyper-V에는 **vCenter 같은 중앙 관리 지점이 없다.**

| 항목 | vCenter | Hyper-V |
|---|---|---|
| 관리 지점 | vCenter 1대가 전체 인벤토리 보유 | 호스트마다 자기 VM만 앎 |
| 연결 단위 | vCenter 1개 = 연결 1개 | **호스트 또는 클러스터 1개 = 연결 1개** |
| 조회 API | PropertyCollector 일괄 조회 | 호스트별 PowerShell/WMI 호출 |
| 인증 | 사용자명+비밀번호 | **NTLM / Kerberos / CredSSP 선택 필요** |

따라서 이 어댑터는 **연결 유형에 따라 수집 범위가 달라진다.**

```python
class ConnectionKind(StrEnum):
    HYPERV_HOST = "hyperv-host"        # 단독 호스트 — 그 호스트의 VM만
    HYPERV_CLUSTER = "hyperv-cluster"  # Failover Cluster — 노드 전체 순회
    SCVMM = "scvmm"                    # 미구현 (CST-09 확정 후)
```

## 3. 모듈 구성

```
src/infrastructure/hyperv/
├── __init__.py          HyperVInventoryReader export
├── reader.py            Protocol 구현 (진입점)
├── session.py           WinRM 세션 관리, 인증 방식 분기
├── commands.py          PowerShell 스크립트 정의
├── wmi_queries.py       WMI 쿼리 정의
├── mapper.py            PowerShell/WMI 출력 → 도메인 모델 변환
└── errors.py            WinRM/WMI 예외 → 도메인 예외 변환
```

## 4. 인증 방식 (FR-103, CST-06) — 이 계획의 핵심 위험

`docs/00_research_notes.md` §7.3: 인증 방식이 환경에 따라 다르며 **잘못 선택하면 접속 자체가 실패한다.**

| 방식 | 사용 조건 | 주의 |
|---|---|---|
| **NTLM** | 도메인 미가입 호스트, 로컬 계정 | 대상에 `TrustedHosts` 등록 필요할 수 있음 |
| **Kerberos** | 도메인 가입 + 도메인 계정 | 포탈 서버도 도메인 가입 또는 krb5 설정 필요 |
| **CredSSP** | 이중 홉(second-hop)이 필요한 경우 | **자격증명이 대상 서버로 위임된다 — 보안 검토 필요** |

```python
class WinRmAuth(StrEnum):
    NTLM = "ntlm"
    KERBEROS = "kerberos"
    CREDSSP = "credssp"
```

**CredSSP 주의**: 자격증명이 원격 서버에 위임되므로 서버가 침해되면 계정이 노출된다.
읽기 전용 계정이라 피해가 제한되지만(NFR-201), UI에서 선택 시 경고를 표시한다 (계획 11).

> **검증 필요** (`docs/00_research_notes.md` §11-5): `pypsrp`의 CredSSP 지원 범위, 비동기 API 제공 여부,
> Kerberos 사용 시 포탈 서버에 필요한 설정을 구현 전 확인한다.

포트·프로토콜: HTTP 5985 / HTTPS 5986. **기본값은 HTTPS**로 하고, HTTP 선택 시 경고를 표시한다.

## 5. 수집 방식

### 5.1 클러스터 연결의 노드 순회

```
1. 클러스터 노드 목록 조회 (Get-ClusterNode)
2. 노드별로 VM 목록 조회
3. 노드 하나가 실패해도 나머지 노드 수집은 계속 (부분 실패 — FR-204)
4. 실패한 노드를 CollectionOutcome에 기록
```

> **검증 필요** (§11-6): Failover Cluster 조회 방식(`FailoverClusters` 모듈 가용성, 클러스터 이름으로의 원격 조회 가능 여부)을 구현 전 확인한다.

### 5.2 PowerShell vs WMI 선택

| 정보 | 권장 경로 | 근거 |
|---|---|---|
| VM 목록·기본 정보 | `Get-VM` | 가장 안정적, 출력 구조가 명확 |
| 네트워크 어댑터·IP | `Get-VMNetworkAdapter` 또는 `Get-VM \| Select -ExpandProperty NetworkAdapters` | 조사 §5.1 확인됨 |
| BIOS UUID | WMI `Msvm_VirtualSystemSettingData.BIOSGUID` | PowerShell로 직접 노출되지 않음 |
| vCPU/메모리 상세 | WMI `Msvm_ProcessorSettingData`, `Msvm_MemorySettingData` | 예약·상한 등 상세값 |
| 게스트 OS·FQDN | **KVP** (§5.3) | 유일한 경로 |

**출력 형식**: PowerShell 결과를 `ConvertTo-Json -Depth N -Compress`로 받아 파싱한다.
텍스트 테이블 파싱은 로케일·폭에 따라 깨지므로 쓰지 않는다.

```powershell
Get-VM | Select-Object Id, Name, State, ProcessorCount, MemoryAssigned, Version, Generation |
    ConvertTo-Json -Depth 4 -Compress
```

**주의**: `ConvertTo-Json`은 항목이 1개일 때 배열이 아닌 객체를 반환한다. 파서에서 정규화한다.

### 5.3 KVP로 게스트 정보 수집 (FR-501) — 주의 지점

`docs/00_research_notes.md` §5.3: 게스트 OS·IP·FQDN은 **KVP 통합 서비스**를 통해서만 얻을 수 있다.

수집 대상 KVP 키:
```
FullyQualifiedDomainName   →  GuestInfo.hostname
OSName                     →  GuestInfo.os_name
OSVersion                  →  GuestInfo.os_version
NetworkAddressIPv4         →  GuestInfo.ipv4_addresses
NetworkAddressIPv6         →  GuestInfo.ipv6_addresses
```

판정 규칙:

```python
def map_guest_info(kvp: dict[str, str], integration_ok: bool) -> GuestInfo:
    if not integration_ok:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)
    if not kvp:
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)
    ...
```

> **검증 필요** (§11-4): Linux 게스트의 KVP 지원 범위가 Windows와 다를 수 있다.
> `hyperv-daemons` 패키지 설치 여부에 따라 KVP가 비어 있을 수 있으므로 대상 환경에서 실측한다.

**`NetworkAddressIPv4`는 세미콜론 구분 문자열**로 오는 경우가 있다. 파싱 후 `src/utils/net.py`의 정규화·필터링을 적용한다.

## 6. 자원 정규화 (계획 02 §6 적용)

| 공통 개념 | Hyper-V 원본 | 처리 |
|---|---|---|
| Cluster | Failover Cluster | 단독 호스트 연결이면 `None` (가상 클러스터를 만들지 않음) |
| Datastore | CSV / SMB 공유 / 로컬 볼륨 | 용량을 바이트로 통일. 경로를 `native_id`로 |
| Network | 가상 스위치 (External/Internal/Private) | `NetworkKind`에 원본 유형 보존 |
| Snapshot | **체크포인트** | 공통 명칭은 스냅샷, UI에서 원본 용어 병기 (NFR-402) |
| ResourcePool | 없음 | `None` + capability False |
| Datacenter/Folder | 없음 | `None` |
| 전원 상태 | Running / Off / Saved / Paused | `ON` / `OFF` / `SUSPENDED` / `SUSPENDED` — `[TODO]` 계획 02 §6 확정 대기 |
| 디스크 프로비저닝 | 동적 확장 / 고정 | Thin / Thick으로 매핑 |
| VM 세대 | Generation 1 / 2 | `PlatformSpec.firmware` = BIOS / UEFI |

**VM GUID**: `Msvm_ComputerSystem.Name`이 VM GUID이며 이것을 `native_id`로 쓴다.
`Get-VM`의 `Id` 속성과 동일하다.

## 7. 연결 테스트 (FR-106)

| 단계 | 방법 |
|---|---|
| REACHABLE | WinRM 포트 TCP 연결 |
| TLS_VALID | HTTPS 사용 시 핸드셰이크 (HTTP면 통과 처리하되 경고) |
| AUTHENTICATED | 간단한 원격 명령 실행 (`$PSVersionTable` 조회 등) |
| AUTHORIZED | `Get-VM` 1건 조회, WMI 네임스페이스 접근 확인 |

**인증 방식이 잘못되면 이 단계에서 걸러진다.** 실패 시 어떤 인증 방식을 시도했는지 결과에 포함하여 관리자가 조정할 수 있게 한다.

## 8. 예외 변환 (`errors.py`)

| 원인 | 도메인 예외 | retryable |
|---|---|---|
| WinRM 인증 실패 (401, `AuthenticationError`) | `AuthenticationError` | **False** |
| 접근 거부 (WMI/PowerShell 권한 부족) | `PermissionError` | False |
| 연결 거부·타임아웃·DNS 실패 | `UnreachableError` | True |
| PowerShell 실행 오류 (cmdlet 없음 등) | `CollectionError` | False |
| 클러스터 노드 일부 실패 | 예외 아님 — `CollectionOutcome`에 기록 | — |

**인증 실패 판정이 특히 중요하다** (FR-114, CST-05). WinRM은 인증 실패를 여러 형태로 보고할 수 있으므로,
HTTP 401뿐 아니라 pypsrp가 던지는 인증 관련 예외 타입을 모두 매핑한다. **모호하면 재시도하지 않는 쪽으로 판정한다.**

## 9. Capability

```python
ReaderCapabilities(
    kind=HypervisorKind.HYPERV,
    supports_resource_pool=False,
    supports_folder_hierarchy=False,
    supports_native_tags=False,          # Notes만 존재
    supports_cluster=(kind == ConnectionKind.HYPERV_CLUSTER),
    supports_incremental=False,
    collectable_types=frozenset(...),
)
```

## 10. 구현 순서

1. **§11 미검증 항목 확인** (pypsrp 기능, KVP Linux 지원, 클러스터 조회) → 결과를 `docs/00_research_notes.md`에 반영
2. `errors.py` → 검증: 인증 실패 → `AuthenticationError(retryable=False)`
3. `session.py` 인증 방식 분기 → 검증: NTLM/Kerberos 연결 (테스트 환경)
4. `commands.py` PowerShell 스크립트 + JSON 파싱 → 검증: 단일 항목/다중 항목 모두 배열로 정규화
5. `wmi_queries.py` → 검증: BIOS GUID, 프로세서·메모리 설정 조회
6. KVP 조회·매핑 → 검증: 통합 서비스 미동작 VM이 올바른 상태로 매핑
7. `mapper.py` 자원별 변환
8. 클러스터 노드 순회 + 부분 실패 → 검증: 노드 1개 실패 시 나머지 수집 계속
9. `reader.py` Protocol 구현 → 검증: **계약 테스트 스위트(계획 03) 통과**

## 11. 완료 기준

- [ ] `arch_check.py` 통과 — vcenter 미참조, **읽기 전용 메서드만 존재**
- [ ] 계약 테스트 스위트 통과 (04와 동일 스위트)
- [ ] NTLM·Kerberos 인증 방식이 설정으로 선택 가능
- [ ] 클러스터 연결에서 노드 순회 수집, 일부 노드 실패 시 부분 성공 처리
- [ ] KVP 미제공 VM이 `TOOLS_NOT_INSTALLED`/`TOOLS_NOT_RUNNING`으로 매핑
- [ ] WinRM/WMI 예외가 어댑터 밖으로 나오지 않음
- [ ] 상태 변경 cmdlet(`Set-VM`, `Stop-VM`, `Remove-VM`, `Checkpoint-VM` 등)이 코드에 존재하지 않음

## 12. 주의사항

- **상태 변경 cmdlet을 절대 실행하지 않는다.** PowerShell은 문자열로 명령을 만들기 때문에 arch-check가 잡지 못한다. `commands.py`에 정의된 스크립트를 verifier가 직접 검토한다 (D-005 한계).
- 원격 PowerShell 호출은 vCenter API보다 훨씬 느리다. **호스트당 호출 횟수를 최소화**하고, 여러 정보를 한 스크립트로 모아 조회한다.
- `ConvertTo-Json`의 단일 항목 문제와 `-Depth` 부족으로 인한 값 누락을 주의한다.
- 로케일에 따라 PowerShell 출력 문자열이 달라질 수 있다. **상태 값 비교는 문자열이 아닌 enum 정수값**(`Msvm_ComputerSystem.EnabledState`)을 우선 사용한다.
- SCVMM 연동은 CST-09 확정 전까지 구현하지 않는다.
- 개발·테스트는 목 커넥터로 한다 (CST-04).
