# 02. 도메인 모델 및 정규화 규칙

> Wave: 1
> 계층: domain (`src/domain/`)
> 담당 요건: FR-301(정규화), FR-302(CI 식별), FR-304(속성 출처), FR-501(수집 불가), `spec.md` §2 속성 카탈로그
> 의존: 01
> 관련 결정: D-002, D-006

## 1. 목적

vCenter와 Hyper-V의 상이한 자원 모델을 담을 **공통 도메인 모델**과, 재수집 시 자원을 동일하게 식별하는 **CI 식별 규칙**을 정의한다.
이 계획의 산출물은 어댑터(04·05), 저장소(06), 유스케이스(07·12) 모두가 참조하는 계약이다.

**도메인은 어디에도 의존하지 않는다.** pyVmomi·SQLAlchemy·FastAPI 타입이 이 계층에 들어오면 안 된다.

## 2. 핵심 설계 판단: "수집 불가"의 표현

`docs/00_research_notes.md` §6에 따르면 게스트 OS·IP·FQDN은 **VMware Tools / Hyper-V 통합 서비스가 동작할 때만** 얻을 수 있고,
셋이 함께 없어진다. 따라서 개별 필드를 `None`으로 두지 않고 **게스트 정보를 하나의 값 객체로 묶고 가용성 상태를 부여**한다.

```python
class GuestInfoAvailability(StrEnum):
    AVAILABLE = "available"                  # 도구 동작 중, 정보 수집됨
    TOOLS_NOT_INSTALLED = "tools_not_installed"
    TOOLS_NOT_RUNNING = "tools_not_running"  # 설치되었으나 미동작(전원 꺼짐 포함)
    UNKNOWN = "unknown"                      # 상태 판정 실패

@dataclass(frozen=True)
class GuestInfo:
    """게스트 OS가 보고하는 정보. 도구 미동작 시 값이 없다."""
    availability: GuestInfoAvailability
    os_name: str | None = None          # 도구 감지값 (권위 있는 값)
    os_version: str | None = None
    hostname: str | None = None         # FQDN
    ipv4_addresses: tuple[str, ...] = ()
    ipv6_addresses: tuple[str, ...] = ()
    tool_version: str | None = None

    @property
    def is_collected(self) -> bool:
        return self.availability is GuestInfoAvailability.AVAILABLE
```

**UI·API 표시 규약 (FR-501)**: `is_collected`가 False면 빈칸이 아니라 "수집 불가 — {사유}"로 표시한다.
사유 문구 매핑은 계획 11에서 정의한다.

**하이퍼바이저 미지원 속성**(Hyper-V의 ResourcePool 등)은 이와 구분된다.
값은 `None`이지만 "수집 불가"가 아니라 "해당 없음"이며, 어댑터의 `capabilities`(계획 03)로 판별한다.

## 3. 자원 엔티티

`spec.md` §2의 속성 카탈로그를 그대로 반영한다. 전체 필드는 카탈로그를 참조하고, 여기서는 구조만 정의한다.

### 3.1 공통 기반

```python
class HypervisorKind(StrEnum):
    VCENTER = "vcenter"
    HYPERV = "hyperv"

class ResourceLifecycle(StrEnum):
    ACTIVE = "active"              # 정상 수집 중
    MISSING = "missing"            # 수집 결과에서 사라짐 — 유예 중 (FR-307)
    RETIRED = "retired"            # 유예 경과 후 폐기
    DISCONNECTED = "disconnected"  # 연결이 삭제됨 (FR-109 권장안)

@dataclass
class ResourceBase:
    resource_id: UUID              # 포탈 생성 ID (불변)
    connection_id: UUID            # 소속 연결 (불변 — FR-110)
    native_id: str                 # 하이퍼바이저 고유 ID
    name: str
    lifecycle: ResourceLifecycle
    first_seen_at: datetime
    last_seen_at: datetime         # 데이터 신선도 (FR-502)
```

### 3.2 엔티티 목록

| 엔티티 | 파일 | 고유 식별자 (native_id) |
|---|---|---|
| `VirtualMachine` | `resource.py` | vCenter `instanceUuid` / Hyper-V VM GUID |
| `Host` | `resource.py` | vCenter MoRef / Hyper-V 호스트 FQDN |
| `Cluster` | `resource.py` | vCenter MoRef / Failover Cluster 이름 |
| `Datastore` | `resource.py` | vCenter MoRef / CSV·공유 경로 |
| `Network` | `resource.py` | vCenter MoRef / 가상 스위치 ID |
| `Snapshot` | `resource.py` | VM ID + 스냅샷 ID |

`VirtualMachine`의 하위 값 객체: `CpuSpec`, `MemorySpec`, `VirtualDisk`, `NetworkAdapter`, `GuestInfo`, `PlatformSpec`.

```python
@dataclass
class VirtualMachine(ResourceBase):
    bios_uuid: str | None
    power_state: PowerState
    connection_state: ConnectionState
    cpu: CpuSpec                        # 총 vCPU, 소켓, 소켓당 코어
    memory: MemorySpec                  # 할당 MB, 동적 메모리 범위(Hyper-V)
    platform: PlatformSpec              # HW 버전, 펌웨어(BIOS/UEFI), 구성값 OS
    guest: GuestInfo                    # 도구 의존 정보 (§2)
    disks: tuple[VirtualDisk, ...]
    adapters: tuple[NetworkAdapter, ...]
    snapshot_summary: SnapshotSummary   # 개수, 최신 생성일, 총 용량
    # 소속 (관계 — FR-306)
    host_native_id: str | None
    cluster_native_id: str | None
    resource_pool: str | None           # Hyper-V는 항상 None (미지원)
    folder_path: str | None             # Hyper-V는 항상 None (미지원)
    # 하이퍼바이저 메타
    annotation: str | None
    native_tags: tuple[str, ...]        # vSphere Tags — 수집 가능성은 §11-7 검증 필요
    created_at: datetime | None
```

### 3.3 포탈 부여 메타데이터 (`metadata.py`)

**수집 데이터와 분리된 별도 엔티티**로 둔다. 같은 테이블에 섞으면 재수집이 덮어쓸 위험이 있다 (FR-602, D-006).

```python
@dataclass
class ResourceMetadata:
    resource_id: UUID
    owner: str | None
    team: str | None
    purpose: str | None
    environment: Environment | None      # PRODUCTION / DEVELOPMENT / TEST / STAGING
    criticality: Criticality | None      # TIER_0 ~ TIER_3
    service_name: str | None
    lifecycle_note: str | None
    tags: frozenset[str]
    updated_by: str
    updated_at: datetime
```

## 4. CI 식별 규칙 (FR-302) — 이 계획의 핵심

`docs/00_research_notes.md` §2.4의 식별 규칙 개념을 구현한다.
**우선순위가 높은 규칙부터 평가하고, 매칭되면 그 자원을 갱신한다. 어느 것도 매칭되지 않을 때만 신규 생성한다.**

```python
class IdentityKey(NamedTuple):
    rule: int          # 1 | 2 | 3
    value: str

def build_identity_keys(vm: VirtualMachine) -> list[IdentityKey]:
    """우선순위 순으로 식별 키를 생성한다."""
    keys = [IdentityKey(1, f"{vm.connection_id}:{vm.native_id}")]
    if vm.bios_uuid:
        keys.append(IdentityKey(2, vm.bios_uuid))
    macs = sorted(a.mac_address for a in vm.adapters if a.mac_address)
    if macs:
        keys.append(IdentityKey(3, f"{'|'.join(macs)}:{vm.name}"))
    return keys
```

| 순위 | 키 | 언제 유효한가 | 한계 |
|---|---|---|---|
| 1 | `connection_id + native_id` | 항상. **연결 ID 불변성(FR-110)이 전제** | 연결을 재등록하면 깨짐 → UI에서 차단 |
| 2 | `bios_uuid` | 클론이 아닌 VM | 클론 시 중복 가능, 미수집 가능 |
| 3 | `MAC 목록 + 이름` | 1·2 실패 시 최후 수단 | MAC 재사용·이름 중복 시 오탐 |

**중요**: 2·3순위 매칭은 **다른 연결의 자원과 매칭될 수 있다.** 이는 동일 자원이 두 연결에서 중복 수집되는 상황이며(FR-308),
자동 병합하지 말고 **경고를 남기고 1순위 기준으로 별도 유지**한다. 잘못된 병합은 되돌리기 어렵다.

## 5. 속성 출처 우선순위 (FR-304)

`docs/00_research_notes.md` §2.5의 조정 규칙. **어떤 소스가 어떤 속성의 권위 있는 소스인지** 정의한다.

| 속성 | 권위 있는 소스 | 대체 소스 | 규칙 |
|---|---|---|---|
| 게스트 OS 이름 | 도구 감지값 (`guest.guestFullName` / KVP `OSName`) | VM 구성값 (`config.guestFullName`) | 도구 값이 있으면 그것을 쓰고, 없으면 구성값을 쓰되 **출처를 표시** |
| 게스트 호스트명 | 도구 감지값 | 없음 | 도구 없으면 수집 불가 |
| IP 주소 | 도구 감지값 | 없음 | 도구 없으면 수집 불가 |
| MAC 주소 | VM 구성 (하이퍼바이저) | — | 도구 불필요 |
| vCPU·메모리·디스크 | VM 구성 (하이퍼바이저) | — | 도구 불필요 |
| **소유자·환경·용도·태그** | **포탈 입력** | — | **수집 데이터가 절대 덮어쓰지 않음** |

```python
class OsSource(StrEnum):
    GUEST_TOOLS = "guest_tools"    # 권위 있는 값
    VM_CONFIG = "vm_config"        # 대체값 — 실제와 다를 수 있음
```

`PlatformSpec`에 `os_source` 필드를 두어 UI가 출처를 표시할 수 있게 한다.

## 6. 정규화 매핑 (FR-301)

어댑터가 참조할 하이퍼바이저 ↔ 공통 모델 대응이다. 속성 단위 매핑은 `spec.md` §2.2에 있다.

| 공통 개념 | vCenter | Hyper-V | 정규화 규칙 |
|---|---|---|---|
| Cluster | ClusterComputeResource | Failover Cluster | 클러스터 미구성 단독 호스트는 **가상 클러스터를 만들지 않고** `cluster_native_id = None`으로 둔다 |
| Datastore | Datastore (VMFS/NFS/vSAN) | CSV / SMB 공유 / 로컬 볼륨 | 용량 단위를 **바이트로 통일**. 프로비저닝 용량은 오버커밋 판단용으로 별도 필드 |
| Network | 표준/분산 포트그룹 | 가상 스위치 | `NetworkKind` enum으로 원본 유형 보존 (NFR-402 원본 용어 병기) |
| Snapshot | Snapshot | 체크포인트 | 공통 명칭은 "스냅샷", UI 상세에서 Hyper-V는 "체크포인트" 병기 |
| ResourcePool | ResourcePool | 없음 | `None` + capability로 미지원 표시 |
| Datacenter/Folder | 있음 | 없음 | `None`. Hyper-V 자원의 논리 그룹은 포탈 메타데이터로 대체 |
| 전원 상태 | poweredOn/poweredOff/suspended | Running/Off/Saved/Paused | `PowerState` 4값으로 통일: `ON`/`OFF`/`SUSPENDED`/`UNKNOWN`. Hyper-V `Paused`→`SUSPENDED` |

> `[TODO]` 확정 필요: Hyper-V `Saved`와 `Paused`를 모두 `SUSPENDED`로 볼지, 별도 값으로 둘지.
> 확정 시 `docs/02_decision.md`에 기록한다.

## 7. 도메인 예외 (`exceptions.py`)

`plans/README.md` §3.5의 계층을 구현한다.
**`AuthenticationError`는 재시도하면 안 되는 예외**임을 클래스 계층으로 드러낸다 (FR-114).

```python
class PortalError(Exception): ...

class ConnectionError(PortalError):
    retryable: bool = True

class AuthenticationError(ConnectionError):
    retryable = False        # 계정 잠금 방지 — 절대 재시도 금지

class UnreachableError(ConnectionError):
    retryable = True

class PermissionError(ConnectionError):
    retryable = False
```

`src/utils/retry.py`의 재시도 데코레이터는 `exc.retryable`을 확인하여 분기한다.

## 8. 구현 순서

1. `exceptions.py` → 검증: 계층 관계·`retryable` 단위 테스트
2. `resource.py` 값 객체(`GuestInfo`, `CpuSpec`, …) → 검증: 불변성·`is_collected` 테스트
3. `resource.py` 엔티티 → 검증: `spec.md` §2.2 필수 속성이 모두 존재하는지 대조
4. `metadata.py` → 검증: 수집 엔티티와 분리되어 있는지
5. 식별 키 생성 함수 → 검증: 우선순위별 키 생성, MAC 정렬 안정성 테스트
6. `connection.py`, `history.py`, `auth.py`, `audit.py` 골격

## 9. 완료 기준

- [ ] `spec.md` §2.2의 **필수(✔) 속성이 모두 엔티티에 존재**
- [ ] 도메인 모듈이 내부 모듈을 import하지 않음 — `arch_check.py` 통과
- [ ] `GuestInfo`로 "값 없음"과 "수집 불가"가 구분됨
- [ ] `build_identity_keys`가 우선순위 순 키를 반환하고, MAC 순서가 달라도 같은 키 생성
- [ ] `AuthenticationError.retryable is False`

## 10. 주의사항

- **엔티티에 SQLAlchemy 모델을 겸하게 하지 않는다.** DB 모델은 계획 06에서 별도 정의하고 매핑한다. 도메인이 인프라에 오염되면 계층 규칙이 무너진다.
- 메타데이터를 자원 엔티티 안에 넣고 싶은 유혹이 있으나, **분리가 FR-602 보존 규칙의 구조적 보장**이다.
- 클론 VM은 BIOS UUID가 중복될 수 있으므로 2순위 규칙을 단독 신뢰하지 않는다.
