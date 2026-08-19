# 04. vCenter 수집 어댑터

> Wave: 2 · 계층: infrastructure (`src/infrastructure/vcenter/`)
> 담당 요건: FR-203, FR-106, FR-301, FR-501, FR-606(부분), `spec.md` §2.2 vCenter 출처 열
> 의존: 02, 03 · 관련 결정: D-003, D-005, D-007, **D-010**

> **진행 현황 (2026-08-16 갱신)**: Step 1 축소판 구현 완료 (2026-08-07) + vcsim 관통 검증 (2026-08-14).
> **실 vCenter 실측(Step 2, ROADMAP §15)은 미완.** 절별 대조·편차·검증 결과는 §13, 완료 기준 판정은 §11을 본다.
>
> **최신 버전 대응 검토 (2026-08-19)**: 이 계획서는 지원 **하한(6.5)** 기준으로만 작성되어 있었고
> **상한(VCF 9.x) 대응 근거가 없었다.** 검토 결과를 §1.2·§3.2·§14에 반영했다. 미해결 결정은 §14.4.

## 1. 목적

pyVmomi로 vCenter 인벤토리를 수집하여 공통 도메인 모델로 반환한다.

**`src/infrastructure/hyperv`를 절대 import하지 않는다** (arch-check 특화 규칙 7).
공통 로직이 필요하면 `src/utils/` 또는 `src/domain/`에 두고 양쪽이 각각 참조한다.

### 1.1 연동 방식 — SOAP 단일 경로 (D-010)

vCenter 접근은 **vSphere Web Services API (SOAP/vim25) 하나만 사용한다.** pyVmomi가 그 Python 바인딩이다.

| 쓰지 않는 방식 | 이유 |
|---|---|
| vSphere Automation API (REST) | 서버측 일괄 조회 메커니즘이 없어 자원 수만큼 왕복 발생 → D-007 위반 |
| VI/JSON | vSphere 8.0 U1+ 전용. 지원 하한이 6.5이므로 사용 불가 (`spec.md` CST-10) |

> **이 전제는 최신 버전에서도 유효하다 (2026-08-19 확인).** vSphere Web Services API(vim25/SOAP)는
> **VCF 9.0에서 deprecated도 removed도 아니다.** VCF SDK 안에서 배포 경로만 `vsphere-ws` → `sdk/vim25`로 바뀌었다.
> 따라서 D-010(SOAP 단일 경로)은 상한을 VCF 9로 올려도 무너지지 않는다. 근거는 §14.5.

**따라서 vSphere Tags는 수집하지 않는다.** Tags는 REST 전용 API이며, `ReaderCapabilities.supports_native_tags = False`는
"조사 미완"이 아니라 **미지원 확정**이다 (계획 03 §3).

FR-606 중 이 어댑터가 담당하는 범위는 **Notes(`config.annotation`) + Custom Attributes(`customValue`)** 두 가지다.
Custom Attributes는 SOAP의 `CustomFieldsManager` 소관이라 REST 없이 수집 가능하다 (§5.2).

> **HTTP 클라이언트를 이 패키지에 들이지 않는다.** `httpx`·`requests`로 vCenter REST를 직접 호출하면
> D-010을 우회하는 것이고, `arch_check.py`의 읽기 전용 검사(메서드명 기반)가 HTTP 동사를 잡지 못한다.

### 1.2 지원 버전 범위 — 하한과 **상한**

`spec.md` CST-10은 **하한(6.5)만** 정의하고 상한을 말하지 않았다. 그 결과 이 계획서 전체가
"6.5에서 되는가"만 따졌고, 고객사가 **VMware Cloud Foundation 9.x**를 쓸 때 깨지는 지점이
검증 항목에 하나도 없었다. 상한을 명시한다.

| 구분 | 버전 | 근거 |
|---|---|---|
| 하한 | vCenter 6.5 | `spec.md` CST-10 (EOL 버전 — pyVmomi 호환 확인 필요, §14.2) |
| **상한** | **vCenter 9.0 (VCF 9.x)** | 최신 GA. 인증·인벤토리 구성이 8.x와 다르다 (§3.2, §5.4) |

**하한과 상한은 서로 다른 위험을 만든다.** 하한은 *속성이 없을* 위험(→ `missingSet`으로 흡수, §4.2),
상한은 *접속 자체가 막히거나 자원 형태가 달라질* 위험이다(→ 흡수 장치 없음). 후자가 더 위험하다.

VCF 9.x에서 이 어댑터에 영향을 주는 변화는 §14에 모았다. 요약하면 4개다.

| # | 변화 | 영향 절 |
|---|---|---|
| 1 | **사용자명+암호 단독 로그인 차단** (페더레이션 우회 방지) | §3.2 |
| 2 | NSX 세그먼트가 `backingType="nsx"` 분산 포트그룹으로 표현 | §5.3, §6.3 |
| 3 | 기본 스토리지가 vSAN — 프로비저닝 공식 전제가 다름 | §5.1 |
| 4 | 제품명 **ESXi → ESX** 환원 | §12 |

## 2. 모듈 구성

```
src/infrastructure/vcenter/
├── __init__.py          VCenterInventoryReader export
├── reader.py            Protocol 구현 (진입점)
├── session.py           SmartConnect 세션 관리
├── collector.py         PropertyCollector 페이징 조회
├── property_specs.py    자원 유형별 수집 속성 목록
├── custom_fields.py     CustomFieldsManager 키→이름 사전 (§5.2)
├── mapper.py            pyVmomi 속성 dict → 도메인 모델
└── errors.py            pyVmomi 예외 → 도메인 예외
```

---

## 3. 세션 관리 (`session.py`)

```python
import ssl
import asyncio
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim


class VCenterSession:
    """vCenter 연결 세션.

    pyVmomi는 동기 라이브러리이므로 모든 호출을 asyncio.to_thread로 오프로드한다.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._si: vim.ServiceInstance | None = None

    @property
    def content(self) -> vim.ServiceInstanceContent:
        if self._si is None:
            raise CollectionError("세션이 열려 있지 않습니다.")
        return self._si.RetrieveContent()

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """TLS 검증 정책에 따른 컨텍스트 (FR-115)."""
        if self._conn.verify_tls:
            return None                              # 기본 검증 사용
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect_sync(self) -> vim.ServiceInstance:
        return SmartConnect(
            host=self._conn.address,
            port=self._conn.port,
            user=self._conn.username,
            pwd=self._conn.password.get_secret_value(),   # 이 지점에서만 복호화
            sslContext=self._build_ssl_context(),
            connectionPoolTimeout=-1,                     # 풀링 비활성 — 세션 수명 직접 관리
        )

    async def start_session(self) -> None:
        try:
            self._si = await asyncio.to_thread(self._connect_sync)
        except Exception as exc:
            raise translate_error(exc) from None          # 원본 예외 체이닝 차단 (자격증명 노출 방지)

    async def close_session(self) -> None:
        if self._si is None:
            return
        try:
            await asyncio.to_thread(Disconnect, self._si)
        except Exception:
            logger.warning("vCenter 세션 종료 실패", extra={"connection_id": str(self._conn.connection_id)})
        finally:
            self._si = None

    async def __aenter__(self) -> "VCenterSession":
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()
```

### 3.1 주의점

- **`raise ... from None`**: pyVmomi 예외 메시지에 접속 정보가 섞일 수 있다. 체이닝을 끊고 정제된 메시지만 남긴다 (계획 10 §2.4).
- **세션 누수**: vCenter는 유휴 세션을 일정 시간 유지한다. 수집 실패 시에도 반드시 `close_session`을 호출한다. `__aexit__`로 보장한다.
- **`connectionPoolTimeout`**: ~~pyVmomi 버전에 따라 파라미터명이 다를 수 있다.~~ **해소 (2026-08-19)** —
  pyVmomi 9.1.0.0 `SmartConnect` 시그니처에 `connectionPoolTimeout`(기본 900)이 그대로 존재한다.
  같은 확인에서 `version` 파라미터가 **`preferredApiVersions`로 바뀐 것**을 확인했으나 이 계획서는
  버전을 명시 지정하지 않으므로 영향 없다 (§14.2).
- vCenter는 HTTPS만 지원한다 (`Connection.validate()`에서 강제, 계획 02 §10).
  VCF 9.0은 **80번 포트 HTTP 엔드포인트를 deprecated** 처리했으므로 이 강제는 상한 쪽과도 일치한다.
- **TLS 하한을 명시한다.** ✅ **적용 완료 (2026-08-19)** — `_build_ssl_context`에
  `ctx.minimum_version = ssl.TLSVersion.TLSv1_2`. VCF 9는 TLS 1.3이 기본이고 기본
  프로파일(`COMPATIBLE`)이 1.2를 폴백으로 남기지만, 강화 프로파일(`NIST_2024_TLS_13_ONLY`)을
  적용한 사이트에서는 클라이언트가 TLS 1.3을 지원해야 한다. `verify_tls=false`는 **인증서
  검증만** 끄는 것이지 프로토콜 하한까지 낮추지 않는다 (`tests/unit/test_vcenter_session_tls.py`).
- **`verify_tls=True` 경로에 CA 번들 지정 수단이 필요하다.** VCF는 VMCA 자체 서명 인증서가 기본이라,
  신뢰 저장소에 VMCA 루트를 넣는 경로가 없으면 운영자가 결국 `verify_tls=false`로 흐른다.
  pyVmomi 9.1에는 `serverPemCert`·`disableSslCertValidation` 파라미터가 있다. `[TODO]` — 계획 02
  `Connection` 모델에 CA 번들 필드를 둘지 Step 2 실측 후 결정한다.

### 3.2 수집 계정 요건 — **VCF 9.0에서 인증이 막힐 수 있다**

VCF 9.0 vSphere 지원 노트 원문:

> "vCenter 9.0 **blocks logins with just a user name and password**, which might sometimes allow
> bypassing the federated provider domain."
> "vCenter 9.0 discontinues support for **Integrated Windows Authentication**."

이 계획서의 `_connect_sync`는 `SmartConnect(user=..., pwd=...)` **단 하나의 인증 경로**만 가정한다.
Identity Federation이 구성된 VCF 9 환경에서 **페더레이션 도메인 계정을 수집 계정으로 쓰면 로그인 자체가 막힌다.**
수집이 실패하는 것이 아니라 연결 등록조차 안 된다.

**따라서 수집 계정 요건을 전제로 명문화한다.**

| 항목 | 요건 |
|---|---|
| 도메인 | **로컬 SSO 도메인(`vsphere.local`) 계정만 사용한다.** AD/IdP 페더레이션 계정은 쓰지 않는다 |
| 역할 | Read-Only (기존 제약 유지) |
| 금지 | IWA·SSPI·스마트카드·RSA SecurID — VCF 9.0에서 전부 제거됨 |
| 주의 | vCenter 9.0에서 **built-in solution user 계정은 deprecated**다. 그 계열을 빌려 쓰지 않는다 |

**대체 경로 `[TODO]`**: 고객사 정책상 로컬 SSO 계정 발급이 불가하면 토큰 인증으로 가야 한다.
pyVmomi 9.1 `SmartConnect`에 `token`·`tokenType`·`sessionId` 파라미터가 존재함을 확인했다
(`b64token`·`mechanism`은 deprecated). 이 경우 계획 02 `Connection` 모델에 **인증 방식 필드**가 필요하며,
자격증명 암호화 대상도 "비밀번호" 하나가 아니게 된다. **Step 2 실측에서 대상 환경의 인증 구성을
먼저 확인한 뒤** 착수 여부를 판단한다 (ROADMAP §15.1).

---

## 4. PropertyCollector 조회 (`collector.py`) — 이 계획의 핵심

`docs/00_research_notes.md` §4.2. **자원을 하나씩 순회하며 개별 속성을 읽으면 안 된다.**

### 4.1 FilterSpec 구성

```python
from pyVmomi import vim, vmodl

PC = vmodl.query.PropertyCollector


def build_filter_spec(
    container_view: vim.view.ContainerView,
    obj_type: type,
    path_set: list[str],
) -> PC.FilterSpec:
    """ContainerView를 순회하며 지정 속성만 조회하는 FilterSpec을 만든다."""
    traversal = PC.TraversalSpec(
        name="traverseEntities",
        path="view",                         # ContainerView.view 속성을 따라 순회
        skip=False,
        type=vim.view.ContainerView,
    )
    obj_spec = PC.ObjectSpec(
        obj=container_view,
        skip=True,                           # 컨테이너 자체는 결과에서 제외
        selectSet=[traversal],
    )
    prop_spec = PC.PropertySpec(
        type=obj_type,
        all=False,                           # 전체 속성 조회 금지 — 응답이 거대해진다
        pathSet=path_set,
    )
    return PC.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])
```

### 4.2 페이징 조회

```python
DEFAULT_PAGE_SIZE = 500


class PropertyCollectorReader:
    def __init__(self, session: VCenterSession, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._session = session
        self._page_size = page_size

    def _create_view_sync(self, obj_type: type) -> vim.view.ContainerView:
        content = self._session.content
        return content.viewManager.CreateContainerView(
            container=content.rootFolder, type=[obj_type], recursive=True
        )

    def _release_view_sync(self, view: vim.view.ContainerView) -> None:
        """ContainerView 자원을 해제한다.

        DestroyView는 뷰 객체 정리이며 하이퍼바이저 자원 삭제가 아니다.
        메서드명 오해를 피하기 위해 래퍼 이름을 release_view로 둔다.
        """
        try:
            view.DestroyView()
        except Exception:
            logger.debug("ContainerView 해제 실패 (무시)")

    def _retrieve_page_sync(
        self, filter_spec: PC.FilterSpec, token: str | None
    ) -> PC.RetrieveResult | None:
        pc = self._session.content.propertyCollector
        if token is None:
            options = PC.RetrieveOptions(maxObjects=self._page_size)
            return pc.RetrievePropertiesEx(specSet=[filter_spec], options=options)
        return pc.ContinueRetrievePropertiesEx(token=token)

    async def retrieve(
        self, obj_type: type, path_set: list[str]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """(MoRef ID, 속성 dict) 튜플을 페이지 단위로 yield한다."""
        view = await asyncio.to_thread(self._create_view_sync, obj_type)
        try:
            filter_spec = build_filter_spec(view, obj_type, path_set)
            token: str | None = None
            while True:
                result = await asyncio.to_thread(self._retrieve_page_sync, filter_spec, token)
                if result is None:
                    break
                for obj_content in result.objects:
                    yield _moref_id(obj_content.obj), _props_to_dict(obj_content)
                token = getattr(result, "token", None)
                if not token:
                    break
        finally:
            await asyncio.to_thread(self._release_view_sync, view)


def _moref_id(managed_object: Any) -> str:
    """Managed Object Reference를 문자열 ID로 변환한다. 예: 'vm-1234'"""
    return str(managed_object._moId)


def _props_to_dict(obj_content: PC.ObjectContent) -> dict[str, Any]:
    """propSet을 dict로 변환하고 missSet(조회 실패 속성)을 None으로 채운다."""
    props: dict[str, Any] = {p.name: p.val for p in (obj_content.propSet or [])}
    for miss in (obj_content.missingSet or []):
        props[miss.path] = None            # 권한 부족·미지원 속성
    return props
```

### 4.3 주의점

- **`token` 반복이 필수다.** 첫 응답만 처리하면 `maxObjects` 초과분이 누락된다. 이 누락은 조용히 발생하므로 테스트로 잡아야 한다.
- **`missingSet` 처리**: 권한 부족이나 버전 미지원 속성은 `propSet`이 아니라 `missingSet`에 온다. 무시하면 `KeyError`가 난다.
- **`maxObjects`가 너무 크면** 응답 크기가 커져 타임아웃이 난다. 500에서 시작해 환경에 맞춰 조정한다.
- **`DestroyView` 누락 시** vCenter에 뷰 객체가 누적된다. `finally`로 보장한다.

---

## 5. 수집 속성 목록 (`property_specs.py`)

**필요한 속성만 명시한다.** `all=True`나 넓은 `pathSet`은 응답을 폭증시킨다.

```python
VM_PROPERTIES: list[str] = [
    "name",
    "config.uuid",                      # BIOS UUID
    "config.instanceUuid",              # native_id (vCenter 인스턴스 내 고유)
    "config.template",                  # 템플릿 여부 — VM과 구분 필요 (§5.4)
    "config.version",                   # vmx-19
    "config.firmware",                  # bios | efi
    "config.guestFullName",             # 구성값 OS
    "config.annotation",
    "config.createDate",
    "config.hardware.numCPU",
    "config.hardware.numCoresPerSocket",
    "config.hardware.memoryMB",
    "config.hardware.device",           # 디스크·NIC 등 전체 장치
    "runtime.powerState",
    "runtime.connectionState",
    "runtime.host",                     # ManagedObjectReference
    "runtime.bootTime",
    "guest.guestFullName",              # 도구 감지값 OS
    "guest.hostName",
    "guest.net",                        # IP 목록
    "guest.toolsStatus",
    "guest.toolsRunningStatus",
    "guest.toolsVersion",
    "snapshot",                         # 스냅샷 트리
    "resourcePool",
    "parent",                           # 폴더
    "customValue",                      # Custom Attributes (FR-606, §5.2)
]

HOST_PROPERTIES: list[str] = [
    "name",
    "runtime.connectionState",
    "runtime.inMaintenanceMode",
    "runtime.bootTime",
    "hardware.systemInfo.vendor",
    "hardware.systemInfo.model",
    "hardware.systemInfo.serialNumber",  # 서비스 태그
    "hardware.cpuInfo.numCpuPackages",
    "hardware.cpuInfo.numCpuCores",
    "hardware.cpuInfo.hz",
    "hardware.cpuPkg",                   # CPU 모델명
    "hardware.memorySize",
    "config.product.name",
    "config.product.version",
    "config.product.build",
    "config.network.pnic",
    "config.network.vnic",               # 관리 IP
    "parent",                            # 클러스터
]

CLUSTER_PROPERTIES: list[str] = [
    "name", "host", "summary.numHosts", "summary.numEffectiveHosts",
    "summary.totalCpu", "summary.totalMemory", "summary.numCpuCores",
    "configuration.dasConfig.enabled",    # HA
    "configuration.drsConfig.enabled",    # DRS
]

DATASTORE_PROPERTIES: list[str] = [
    "name", "summary.type", "summary.capacity", "summary.freeSpace",
    "summary.uncommitted",                # 프로비저닝 초과분 (오버커밋 판단)
    "summary.url", "summary.accessible", "host",
]

NETWORK_PROPERTIES: list[str] = ["name", "summary.accessible", "host", "vm"]
DVPG_PROPERTIES: list[str] = [
    "name", "config.defaultPortConfig", "config.distributedVirtualSwitch", "host",
    # --- NSX 식별 (VCF 필수, §5.3) ---
    "config.backingType",               # "standard" | "nsx"
    "config.logicalSwitchUuid",
    "config.segmentId",
    "config.transportZoneUuid",
]
```

> **[검증 필요]** (`docs/00_research_notes.md` §11-2): 속성 경로는 vSphere 버전에 따라 존재 여부가 다르다.
> **하한·상한 양쪽에서 실측한다** (§1.2).
> - **하한(6.5)**: `config.createDate`처럼 후속 버전에서 추가된 속성이 `missingSet`으로 떨어질 수 있다.
> - **상한(VCF 9.0)**: 위 목록의 속성 경로는 pyVmomi 9.1.0.0 바인딩에 **전부 존재함을 확인했다 (2026-08-19)**.
>   `guest.toolsStatus`는 API 4.0부터 deprecated이지만 9.1 바인딩에도 남아 있고 값도 채워진다 (§12).
>
> 누락 속성은 예외가 아닌 `None`으로 처리한다 (§4.2 `_props_to_dict`가 자동 대응).
> **이 흡수 장치는 "속성이 없는" 경우만 막아 준다.** 자원의 표현 형태 자체가 달라지는 경우(§5.3 NSX,
> §5.1 vSAN)는 조용히 잘못된 값을 만들므로 별도 대응이 필요하다.

### 5.1 프로비저닝 용량 계산

`summary.uncommitted`는 Thin 디스크의 미할당분이다. 프로비저닝 총량은:

```
provisioned = (capacity - freeSpace) + uncommitted
```

> **이 공식은 VMFS/NFS 전제다. vSAN에서는 그대로 쓰면 안 된다. `[검증 필요]`**
> **VCF의 기본 스토리지는 vSAN**이므로 상한 환경에서는 이쪽이 주 경로가 된다.
> vSAN은 스토리지 정책(FTT/RAID)에 따라 실제 소비량이 배수로 달라지고, `summary.uncommitted`가
> 채워지지 않을 수 있다. Step 2에서 `summary.type == "vsan"`인 데이터스토어의 `capacity`·`freeSpace`·
> `uncommitted` 실값을 확인하기 전까지 **vSAN 데이터스토어는 `provisioned`를 계산하지 않고 `None`으로 둔다.**
> 잘못 계산한 오버커밋 수치는 빈 값보다 나쁘다.
>
> 참고: **vVols는 VCF 9.0에서 deprecated**로 공지되어 향후 제거 예정이다. 신규 대응하지 않는다.

### 5.2 Custom Attributes (`custom_fields.py`) — FR-606

`customValue`는 **키(int)와 값(str) 쌍만** 담고 있다. 필드 이름은 전역 `CustomFieldsManager`에 따로 있으므로
**수집 시작 시 1회 조회하여 키→이름 사전을 만들고** 매핑 때 대조한다.

```python
async def load_custom_field_names(session: VCenterSession) -> dict[int, str]:
    """CustomFieldsManager에서 키→필드명 사전을 만든다. 수집 1회당 1회 호출.

    조회 전용이다. SetField·AddCustomFieldDef·RemoveCustomFieldDef는 절대 호출하지 않는다 (D-005).
    """
    def _load_sync() -> dict[int, str]:
        manager = session.content.customFieldsManager
        if manager is None:                      # 권한 부족 시 None이 올 수 있다
            return {}
        return {f.key: f.name for f in (manager.field or [])}

    try:
        return await asyncio.to_thread(_load_sync)
    except Exception:
        logger.info("Custom Attributes 조회 불가 — 빈 사전으로 진행")
        return {}                                # 부분 실패 허용 — 나머지 수집은 정상 진행
```

매핑:

```python
def map_custom_attributes(
    props: dict[str, Any], field_names: dict[int, str]
) -> tuple[tuple[str, str], ...]:
    """customValue(키·값)를 (이름, 값) 쌍으로 변환한다. 이름을 못 찾은 키는 버린다."""
    return tuple(
        (field_names[v.key], v.value)
        for v in (props.get("customValue") or [])
        if v.key in field_names and v.value
    )
```

**주의**

- **vSphere Tags와 다른 개념이다.** Custom Attributes는 SOAP(`CustomFieldsManager`), Tags는 REST 전용이다.
  이름이 비슷해 혼동하기 쉽다. 이 어댑터는 Custom Attributes만 수집한다 (§1.1, D-010).
- `CustomFieldsManager`에는 쓰기 메서드(`SetField` 등)가 있다. **읽기(`field` 속성)만 접근한다.**
  §12의 `grep` 검사 대상에 `SetField`·`AddCustomFieldDef`·`RemoveCustomFieldDef`를 포함한다.
- **[검증 필요]** Read-Only 역할 계정에서 `customFieldsManager.field`와 `customValue`가 조회되는지 실환경 확인.
  조회되지 않으면 FR-606의 남은 절반도 수집 불가가 되며, 이때는 빈 값으로 두고 진행한다 (예외를 던지지 않는다).
- 포탈 메타데이터를 **덮어쓰지 않는다.** 이 값은 참고용 초기값이며 `ResourceMetadata`와 별개 필드로 저장한다
  (계획 02 §8, FR-602).

### 5.3 네트워크 자원 수집 — 중복과 NSX (VCF 필수)

**문제 1 — 같은 포트그룹이 두 번 수집된다.**

pyVmomi 9.1 바인딩에서 타입 계층을 확인한 결과다 (2026-08-19).

```
vim.dvs.DistributedVirtualPortgroup → vim.Network → vim.ManagedEntity
vim.OpaqueNetwork                   → vim.Network → vim.ManagedEntity
```

**DVPG와 OpaqueNetwork는 둘 다 `vim.Network`의 하위 타입이다.** 따라서
`CreateContainerView(type=[vim.Network])`는 표준 포트그룹뿐 아니라 **분산 포트그룹과 opaque network를
함께 반환한다.** §5의 `NETWORK_PROPERTIES`와 `DVPG_PROPERTIES`를 각각 수집하면
**동일 포트그룹이 두 레코드로 저장된다** — CLAUDE.md "자원 식별 일관성" 제약(중복 레코드 = 결함) 위반이다.

**규칙**: Network 수집 결과에서 **MoRef 접두사로 판별해 DVPG·OpaqueNetwork를 제외**한다.
표준 포트그룹은 `network-*`, 분산 포트그룹은 `dvportgroup-*`이다. 제외한 것은 DVPG 전용 수집으로만 담는다.

```python
def is_standard_network(moid: str) -> bool:
    """vim.Network 뷰에는 DVPG·OpaqueNetwork가 함께 온다. 표준 포트그룹만 남긴다."""
    return moid.startswith("network-")
```

> `[검증 필요]` MoRef 접두사 규칙은 vCenter 구현 세부사항이다. Step 2에서 **실제 반환된 MoRef 접두사
> 분포**를 기록한다. 접두사가 신뢰할 수 없으면 `PropertySpec`에 타입별 `pathSet`을 나눠 넣어
> `missingSet` 유무로 판별하는 방식으로 바꾼다.

**문제 2 — VCF의 NSX 세그먼트를 구분할 수 없다.**

VCF는 NSX가 기본 구성요소다. NSX 세그먼트는 **VDS 위에서 `backingType="nsx"`인 분산 포트그룹**으로
나타난다(N-VDS/opaque 방식은 구형). pyVmomi 9.1의 `DistributedVirtualPortgroup.ConfigInfo` 실제 필드를
확인했다.

```
key, name, numPorts, distributedVirtualSwitch, defaultPortConfig, description, type,
backingType, policy, ..., transportZoneUuid, transportZoneName, logicalSwitchUuid,
segmentId, subnetId, nsxConfig
```

§5의 `DVPG_PROPERTIES`에 `config.backingType`·`config.logicalSwitchUuid`·`config.segmentId`·
`config.transportZoneUuid`를 추가했다. 이것이 없으면 **VCF 환경에서 NSX 세그먼트와 일반 포트그룹이
구별되지 않는다.**

### 5.4 VCF 환경의 인벤토리 노이즈

`vim.VirtualMachine` 뷰에는 운영자가 관리하는 VM 외에 다음이 함께 들어온다. VCF는 이들이
**기본 배포물**이라 8.x 환경보다 규모가 크다.

| 대상 | 판별 | 조치 |
|---|---|---|
| **VM 템플릿** | `config.template == True` | 별도 분류. §5에 `config.template` 추가함 |
| vCLS VM | 이름 `vCLS-*` | VCF 9.0에서 deprecated지만 8.x 환경엔 존재 |
| Supervisor / VKS 노드 VM, vSphere Pod | 폴더·네임스페이스 소속 | 분류만 하고 제외하지 않는다 |
| VCF Operations·Automation 어플라이언스 | 이름 규칙 | 분류만 |

**제외가 아니라 분류다.** 인벤토리 포탈은 "무엇이 있는지"를 보여주는 것이 목적이므로
임의로 숨기지 않는다. 다만 목록 기본 필터와 리포트 집계에서 템플릿·인프라 VM이 섞이면 수치가 왜곡되므로
**구분 가능한 필드는 반드시 수집한다.** 판별 규칙 확정은 Step 2 실측 후 (`docs/04_field_validation.md`).

---

## 6. 매핑 (`mapper.py`)

### 6.1 VM 매핑

```python
def map_virtual_machine(
    connection_id: UUID,
    moid: str,
    props: dict[str, Any],
    observed_at: datetime,
    field_names: dict[int, str],          # §5.2 — 수집 시작 시 1회 로드
) -> VirtualMachine:
    devices = props.get("config.hardware.device") or []
    disks = tuple(_map_disk(d) for d in devices if isinstance(d, vim.vm.device.VirtualDisk))
    adapters = tuple(
        _map_adapter(d) for d in devices if isinstance(d, vim.vm.device.VirtualEthernetCard)
    )

    return VirtualMachine(
        resource_id=uuid4(),                       # 저장소가 기존 자원 매칭 시 교체
        connection_id=connection_id,
        native_id=props.get("config.instanceUuid") or moid,
        name=props.get("name") or moid,
        bios_uuid=props.get("config.uuid"),
        power_state=VCENTER_POWER_MAP.get(str(props.get("runtime.powerState")), PowerState.UNKNOWN),
        connection_state=_map_connection_state(props.get("runtime.connectionState")),
        boot_time=props.get("runtime.bootTime"),
        cpu=CpuSpec(
            total_vcpu=props.get("config.hardware.numCPU") or 0,
            cores_per_socket=props.get("config.hardware.numCoresPerSocket"),
            socket_count=_socket_count(props),
        ),
        memory=MemorySpec(assigned_mb=props.get("config.hardware.memoryMB") or 0),
        platform=PlatformSpec(
            hardware_version=props.get("config.version"),
            firmware=Firmware.UEFI if props.get("config.firmware") == "efi" else Firmware.BIOS,
            configured_os=props.get("config.guestFullName"),
        ),
        guest=map_guest_info(props, observed_at),
        disks=disks,
        adapters=adapters,
        snapshots=_map_snapshot_summary(props.get("snapshot")),
        host_native_id=_moref_or_none(props.get("runtime.host")),
        cluster_native_id=None,                    # 호스트→클러스터 해석은 저장소/조회 시점
        resource_pool=_moref_or_none(props.get("resourcePool")),
        folder_path=None,                          # parent 체인 해석은 §6.4
        annotation=props.get("config.annotation"),
        custom_attributes=map_custom_attributes(props, field_names),   # §5.2 (vSphere Tags 아님)
        created_at=props.get("config.createDate"),
        last_seen_at=observed_at,
    )


def _socket_count(props: dict[str, Any]) -> int | None:
    total = props.get("config.hardware.numCPU")
    per_socket = props.get("config.hardware.numCoresPerSocket")
    if not total or not per_socket:
        return None
    return total // per_socket
```

### 6.2 게스트 정보 매핑 (FR-501) — 주의 지점

`docs/00_research_notes.md` §6: Tools가 없으면 IP·OS·호스트명을 얻을 수 없다.
§6.3: Tools 상태 보고가 부정확한 알려진 버그가 있으므로 **상태만 믿지 말고 값 유무도 함께 본다.**

```python
def map_guest_info(props: dict[str, Any], observed_at: datetime) -> GuestInfo:
    tools_status = props.get("guest.toolsStatus")
    running = props.get("guest.toolsRunningStatus")

    if tools_status == "toolsNotInstalled":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)

    if running is not None and running != "guestToolsRunning":
        return GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    hostname = props.get("guest.hostName")
    nets = props.get("guest.net") or []
    raw_ips = [ip for n in nets for ip in (getattr(n, "ipAddress", None) or [])]
    v4, v6 = split_ip_families(raw_ips)             # 링크로컬·루프백 제거 (계획 02 §9.3)

    if not hostname and not v4 and not v6:
        # 상태는 정상인데 값이 없음 — 부팅 직후일 수 있다
        return GuestInfo(availability=GuestInfoAvailability.UNKNOWN)

    os_name, os_source = resolve_os_name(
        props.get("guest.guestFullName"), props.get("config.guestFullName")
    )
    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name=os_name,
        os_source=os_source,
        hostname=hostname,
        ipv4_addresses=v4,
        ipv6_addresses=v6,
        tool_version=props.get("guest.toolsVersion"),
        observed_at=observed_at,
    )
```

**`guest.net[].ipAddress`에는 링크로컬(`fe80::`)이 포함된다.** 필터링하지 않으면 목록·검색이 오염된다.

### 6.3 장치 매핑

```python
def _map_disk(dev: vim.vm.device.VirtualDisk) -> VirtualDisk:
    backing = dev.backing
    thin = getattr(backing, "thinProvisioned", None)
    return VirtualDisk(
        key=str(dev.key),
        label=getattr(dev.deviceInfo, "label", None),
        provisioned_bytes=(dev.capacityInKB or 0) * 1024,
        used_bytes=None,                    # Thin 실제 사용량 — §6.5 참조
        provisioning=(
            DiskProvisioning.THIN if thin is True
            else DiskProvisioning.THICK if thin is False
            else DiskProvisioning.UNKNOWN
        ),
        datastore_name=_datastore_name_from_path(getattr(backing, "fileName", None)),
        file_path=getattr(backing, "fileName", None),
    )


ADAPTER_TYPE_MAP = {
    vim.vm.device.VirtualVmxnet3: "vmxnet3",
    vim.vm.device.VirtualVmxnet2: "vmxnet2",
    vim.vm.device.VirtualE1000e: "e1000e",
    vim.vm.device.VirtualE1000: "e1000",
    vim.vm.device.VirtualPCNet32: "pcnet32",
}


def _map_adapter(dev: vim.vm.device.VirtualEthernetCard) -> NetworkAdapter:
    return NetworkAdapter(
        key=str(dev.key),
        mac_address=normalize_mac(dev.macAddress),           # 계획 02 §9.3
        adapter_type=next(
            (v for k, v in ADAPTER_TYPE_MAP.items() if isinstance(dev, k)),
            type(dev).__name__,
        ),
        network_name=_network_name(dev.backing),
        connected=getattr(dev.connectable, "connected", None),
    )


def _network_name(backing: Any) -> str | None:
    """백킹 종류마다 이름 추출 경로가 다르다. 3가지를 모두 처리한다.

    pyVmomi 9.1 바인딩 확인 결과 (2026-08-19):
      NetworkBackingInfo                 → deviceName        (표준 포트그룹)
      DistributedVirtualPortBackingInfo  → port.portgroupKey (분산 포트그룹 / NSX 세그먼트)
      OpaqueNetworkBackingInfo           → opaqueNetworkId   (NSX opaque network)
    """
    if hasattr(backing, "deviceName"):                       # 표준 포트그룹
        return backing.deviceName
    port = getattr(backing, "port", None)                    # 분산 포트그룹
    if port is not None:
        return getattr(port, "portgroupKey", None)           # 키 → 이름 해석은 §6.4
    return getattr(backing, "opaqueNetworkId", None)         # opaque network (§5.3)
```

> 분산 포트그룹은 `portgroupKey`(MoRef)만 얻어진다. 이름으로 바꾸려면 별도로 수집한 Network 목록과 대조해야 한다.
> **매핑 단계에서 키를 저장하고, 이름 해석은 수집 완료 후 후처리**로 수행한다 (§6.4).

> **opaque 분기가 없으면 조용히 `None`이 된다.** 기존 코드는 `deviceName`도 `port`도 없는 백킹에서
> `getattr(None, "portgroupKey", None)`을 평가해 예외 없이 `None`을 반환했다. NSX opaque network를 쓰는
> VM의 네트워크 이름이 **아무 오류 없이 통째로 비는** 형태라 감지되지 않는다. 위 3분기로 고정하고,
> 셋 중 어디에도 걸리지 않으면 `[검증 필요]`로 로그를 남긴다.

### 6.4 참조 해석 후처리

VM 매핑 시점에는 호스트·클러스터·폴더·포트그룹 이름을 모른다.
**수집 순서에 의존하지 않도록 MoRef를 저장하고, 조회 시점에 해석한다** (계획 06 §5.1).

단, 클러스터는 `호스트 → 클러스터` 관계로만 알 수 있으므로 수집 후처리에서 채운다.

```python
def resolve_vm_cluster(vms: list[VirtualMachine], hosts: dict[str, Host]) -> None:
    """호스트 MoRef로 클러스터를 역참조한다. 수집 완료 후 실행."""
    for i, vm in enumerate(vms):
        host = hosts.get(vm.host_native_id or "")
        if host and host.cluster_native_id:
            vms[i] = replace(vm, cluster_native_id=host.cluster_native_id)
```

### 6.5 Thin 디스크 실제 사용량

`capacityInKB`는 프로비저닝 용량이다. 실제 사용량은 별도 경로가 필요하다.

**1차 구현에서는 `used_bytes=None`으로 둔다.** 확보 방법(`vim.vm.Summary.StorageSummary.committed` 등)은
`[TODO]`로 남기고, 필요해지면 별도로 조사한다. 용량 리포트(FR-805)는 데이터스토어 레벨 값으로 대체 가능하다.

---

## 7. 연결 테스트 (`reader.py`)

```python
async def check_connection(self) -> ConnectionCheckResult:
    runner = StageRunner()                                   # 계획 03 §4.1

    await runner.run(CheckStage.REACHABLE, self._check_reachable)
    await runner.run(CheckStage.TLS_VALID, self._check_tls)
    await runner.run(CheckStage.AUTHENTICATED, self._check_auth)

    readable: set[ResourceType] = set()
    await runner.run(CheckStage.AUTHORIZED, lambda: self._check_authorized(readable))

    return ConnectionCheckResult(
        stages=runner.results,
        readable_types=frozenset(readable),
        server_version=self._server_version,
    )


async def _check_reachable(self) -> str | None:
    """TCP 연결만 확인한다. 짧은 타임아웃을 쓴다."""
    fut = asyncio.open_connection(self._conn.address, self._conn.port)
    reader, writer = await asyncio.wait_for(fut, timeout=5)
    writer.close()
    await writer.wait_closed()
    return f"{self._conn.address}:{self._conn.port}"


async def _check_authorized(self, readable: set[ResourceType]) -> str | None:
    """자원 유형별로 1건만 조회하여 권한을 확인한다. 전량 조회는 부적절하다."""
    for rtype, obj_type, props in (
        (ResourceType.VIRTUAL_MACHINE, vim.VirtualMachine, ["name"]),
        (ResourceType.HOST, vim.HostSystem, ["name"]),
        (ResourceType.DATASTORE, vim.Datastore, ["name"]),
        (ResourceType.NETWORK, vim.Network, ["name"]),
    ):
        try:
            probe = PropertyCollectorReader(self._session, page_size=1)
            async for _ in probe.retrieve(obj_type, props):
                break
            readable.add(rtype)
        except vim.fault.NoPermission:
            continue                                          # 이 유형만 실패, 나머지 계속
    if not readable:
        raise PermissionError("조회 권한이 있는 자원 유형이 없습니다.")
    return f"조회 가능: {', '.join(sorted(t.value for t in readable))}"
```

---

## 8. 예외 변환 (`errors.py`)

**pyVmomi 예외가 어댑터 밖으로 나가면 안 된다.**

```python
def translate_error(exc: Exception) -> PortalError:
    """pyVmomi 예외를 도메인 예외로 변환한다. 메시지에서 자격증명을 제거한다."""
    if isinstance(exc, vim.fault.InvalidLogin):
        return AuthenticationError("인증에 실패했습니다. 계정 또는 비밀번호를 확인하세요.")
    if isinstance(exc, vim.fault.NoPermission):
        priv = getattr(exc, "privilegeId", None)
        return PermissionError(
            "조회 권한이 부족합니다." + (f" (필요 권한: {priv})" if priv else "")
        )
    if isinstance(exc, vim.fault.HostConnectFault):
        return UnreachableError("vCenter에 연결할 수 없습니다.")
    if isinstance(exc, (ssl.SSLError, ssl.SSLCertVerificationError)):
        return UnreachableError(
            "TLS 인증서 검증에 실패했습니다. 자체 서명 인증서라면 검증을 비활성화하세요."
        )
    if isinstance(exc, (socket.timeout, asyncio.TimeoutError, TimeoutError)):
        return UnreachableError("응답 시간이 초과되었습니다.")
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, OSError)):
        return UnreachableError("네트워크 연결에 실패했습니다.")
    if isinstance(exc, vmodl.MethodFault):
        return CollectionError(f"vCenter 오류: {sanitize_message(getattr(exc, 'msg', ''))}")
    return CollectionError(f"알 수 없는 오류: {sanitize_message(str(exc))}")
```

| pyVmomi 예외 | 도메인 예외 | retryable |
|---|---|---|
| `vim.fault.InvalidLogin` | `AuthenticationError` | **False** |
| `vim.fault.NoPermission` | `PermissionError` | False |
| `vim.fault.HostConnectFault` | `UnreachableError` | True |
| SSL 오류 | `UnreachableError` | True |
| 타임아웃·소켓 오류 | `UnreachableError` | True |
| 기타 `vmodl.MethodFault` | `CollectionError` | False |

**`InvalidLogin` → `AuthenticationError` 매핑이 계정 잠금 방지의 출발점이다** (FR-114, CST-05).

`sanitize_message`는 계획 10 §2.5의 마스킹 패턴을 재사용한다.

---

## 9. Reader 구현 (`reader.py`)

```python
class VCenterInventoryReader:
    """HypervisorInventoryReader 구현 (vCenter)."""

    def __init__(self, connection: Connection, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._conn = connection
        self._session = VCenterSession(connection)
        self._pc = PropertyCollectorReader(self._session, page_size)
        self._outcomes: list[CollectionOutcome] = []
        self._host_cache: dict[str, Host] = {}
        self._field_names: dict[int, str] = {}       # §5.2 — start_session 직후 1회 로드

    @property
    def capabilities(self) -> ReaderCapabilities:
        return VCENTER_CAPABILITIES              # supports_native_tags=False 확정 (D-010)

    async def start_session(self) -> None:
        await self._session.start_session()
        self._field_names = await load_custom_field_names(self._session)   # §5.2 — 1회만

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        observed_at = datetime.now(UTC)
        started = time.monotonic()
        count = 0
        try:
            async for moid, props in self._pc.retrieve(vim.VirtualMachine, VM_PROPERTIES):
                count += 1
                yield map_virtual_machine(
                    self._conn.connection_id, moid, props, observed_at, self._field_names
                )
        except AuthenticationError:
            raise                                    # 세션 무효 — 전파
        except PortalError as exc:
            self._record(ResourceType.VIRTUAL_MACHINE, count, failed=True, error=str(exc))
            return
        self._record(ResourceType.VIRTUAL_MACHINE, count, failed=False, started=started)
```

나머지 `list_*`도 동일 패턴이다. 중복을 줄이려면 제네릭 헬퍼로 감싼다.

```python
async def _collect(
    self, rtype: ResourceType, obj_type: type, props: list[str],
    mapper: Callable[[str, dict[str, Any], datetime], T],
) -> AsyncIterator[T]:
    """수집 공통 로직: 예외를 outcome으로 전환하고 통계를 기록한다."""
```

---

## 10. 구현 순서

| # | 작업 | 검증 | 상태 (2026-08-16) |
|---|---|---|---|
| 1 | `errors.py` | `InvalidLogin` → `AuthenticationError(retryable=False)`, 메시지에 자격증명 없음 | ✅ 완료 |
| 2 | `session.py` | 연결/해제, 실패 경로에서도 세션 정리, TLS 검증 on/off | ✅ 완료 |
| 3 | `property_specs.py` | **대상 vCenter에서 모든 속성 경로 실측** (§5 검증 필요) — **하한·상한 양쪽** | 🔶 축소판(`VM_PROPERTIES_MVP` 13개)만 — 실측은 Step 2 |
| 4 | `collector.py` 페이징 | `maxObjects`(=2) 초과 자원에서 토큰 반복 동작, `missingSet` 처리 | ✅ 완료 — 단위 테스트로 확인 |
| 5 | `mapper.py` — `map_guest_info` | Tools 상태 4가지 분기, 링크로컬 필터 | 🔶 4분기 완료 — IP 미수집이라 링크로컬 필터는 미해당 |
| 5b | `custom_fields.py` | 키→이름 사전 로드, manager가 `None`일 때 빈 사전, 이름 없는 키 폐기 | ⬜ 미착수 (Step 8) |
| 6 | `mapper.py` — 장치 | 디스크 Thin/Thick, MAC 정규화, 분산 포트그룹 키 | ⬜ 미착수 (Step 4) |
| 7 | `mapper.py` — VM 전체 | `spec.md` §2.2 필수 속성 매핑 | 🔶 Step 1 필드만 (§13.1) |
| 8 | `mapper.py` — Host/Cluster/Datastore/Network | 용량 바이트 통일, 오버커밋 계산 | ⬜ 미착수 (Step 6) |
| 9 | `reader.py` | **계약 테스트 스위트(계획 03 §9) 통과** | 🔶 `list_virtual_machines`만 — Protocol 준수 계약 통과 |
| 10 | 연결 테스트 | 4단계 결과, 권한 부족 시 유형별 판정 | 🔶 4단계 구현 — AUTHORIZED는 VM 1종만 프로브 (§13.2) |
| 11 | **상한(VCF 9.x) 대응** — §3.2 계정 요건, §5.3 NSX·중복, §5.1 vSAN, §6.3 opaque 분기 | §14.3의 7개 항목 실측 | ⬜ 미착수 (Step 2, D-020) |

## 11. 완료 기준

> 판정일 2026-08-16. Step 1 범위 밖 항목은 사유와 해당 Step을 병기한다.

- [x] `arch_check.py` 통과 — hyperv 미참조, 읽기 전용 메서드만 *(`--ci` 재실행, 위반 0)*
- [ ] 계약 테스트 스위트 14종 통과 (05와 동일 스위트) — *Protocol 준수 계약(세 어댑터 파라미터화, `tests/unit/test_hyperv_readers.py`)은 통과. 행동 계약 스위트의 vCenter 적용은 잔여*
- [x] `maxObjects` 초과 자원이 전량 수집됨 (토큰 반복 확인) — *`tests/unit/test_vcenter_collector.py` (page_size=2, 3페이지 목)*
- [x] `missingSet` 속성이 `KeyError` 없이 `None`으로 처리됨 — *동일 테스트 파일*
- [x] Tools 미설치 VM이 `TOOLS_NOT_INSTALLED`로 매핑 — *`tests/unit/test_vcenter_mapper.py` + vcsim 실동작(게스트 도구 없음)*
- [ ] 링크로컬·루프백 IP가 결과에 없음 — *미해당: Step 1은 `guest.net`을 수집하지 않는다. IP 수집 추가 시 확인*
- [ ] MAC이 `00:50:56:aa:bb:cc` 형식으로 정규화 — *미해당: NIC 매핑은 Step 4 (§6.3)*
- [x] pyVmomi 예외가 어댑터 밖으로 나오지 않음 — *공개 경로(세션·수집·연결 테스트) 전부 `translate_error` 경유. 전용 테스트는 없음(코드 검토)*
- [x] 세션이 실패 경로에서도 해제됨 (`__aexit__` 확인) — *reader `__aexit__` + `collect_service` `finally`. 뷰 해제는 오류 주입 테스트로 확인*
- [ ] Custom Attributes가 (이름, 값) 쌍으로 매핑되고, 조회 실패 시에도 나머지 수집이 진행됨 — *미착수 (Step 8, §5.2)*
- [x] **패키지에 HTTP 클라이언트 import가 없음** — `httpx`·`requests`·`aiohttp` (D-010: REST 우회 금지) *(grep 확인)*
- [x] **코드에 쓰기 API 호출이 없음** — `Destroy_Task`, `PowerOffVM_Task`, `ReconfigVM_Task`, `CreateVM_Task`, `RelocateVM_Task`, `SetField`, `AddCustomFieldDef`, `RemoveCustomFieldDef` 등 (verifier가 직접 확인, D-005 한계) *(grep 확인 — 호출 0건, docstring 언급 2건뿐)*

**상한(VCF 9.x) 대응 — 2026-08-19 추가.** 전부 Step 2 실측 이후 판정한다 (§14.3).

- [x] `pyproject.toml`의 `pyvmomi`에 **하한·상한 핀이 있음** (§14.2) — `>=8.0.3,<10` *(2026-08-19)*
- [ ] 수집 계정이 **로컬 SSO 도메인 계정**임이 문서로 확인됨 (§3.2)
- [ ] `vim.Network` 수집 결과에 **DVPG·OpaqueNetwork가 섞이지 않음** (§5.3)
- [ ] NSX 세그먼트가 `config.backingType == "nsx"`로 식별됨 (§5.3)
- [ ] NSX opaque network를 쓰는 VM의 네트워크 이름이 비지 않음 (§6.3)
- [ ] vSAN 데이터스토어의 `provisioned`가 **잘못 계산되지 않음** (미검증 시 `None`, §5.1)
- [ ] VM 템플릿이 일반 VM과 구분됨 (`config.template`, §5.4)
- [x] `_build_ssl_context`에 `minimum_version`이 명시됨 (§3.1) — TLS 1.2 하한 *(2026-08-19)*

## 12. 주의사항

- **쓰기 API 호출 금지.** arch-check는 메서드명만 보므로 `get_vm_status()` 안의 `PowerOffVM_Task()`를 잡지 못한다. `grep -rE "_Task\(|SetField|CustomFieldDef" src/infrastructure/vcenter/`로 확인하고, 조회용 `_Task`(없음)를 제외한 모든 사용을 검토한다.
- **vSphere Tags를 구현하지 않는다** (D-010). "태그가 안 보인다"는 지적을 받으면 REST API 도입 결정을 먼저 받는다. `httpx`로 `/rest/com/vmware/cis/tagging`을 호출하는 것은 D-010 우회이며, 읽기 전용 자동 검사가 HTTP 동사를 잡지 못한다.
- `DestroyView`는 뷰 정리이지 자원 삭제가 아니다. 래퍼 이름을 `release_view`로 두어 오해를 막는다.
- pyVmomi는 동기 라이브러리다. `asyncio.to_thread` 없이 호출하면 이벤트 루프가 멈춘다.
- `raise ... from None`으로 예외 체이닝을 끊는다. 원본 메시지에 접속 정보가 섞일 수 있다.
- 개발·테스트는 목 커넥터로 한다 (CST-04). 운영 vCenter에 붙지 않는다.
- **`"ESXi"` 문자열로 하이퍼바이저 종류를 판별하지 않는다.** VCF 9.0에서 제품명이 **ESXi → ESX로 환원**되었다.
  이 어댑터는 `config.product.name`을 가공 없이 저장하므로 안전하지만, 조회·리포트 계층에서
  `"ESXi"` 매칭을 넣으면 VCF 9 호스트가 걸러진다 (`spec.md` §2의 "하이퍼바이저 종류(ESXi/Hyper-V)" 표기 포함).
  종류 판별은 **`ConnectionKind`로 한다** — 제품명 문자열이 아니다.
- **deprecated 속성을 알고 쓴다.** `guest.toolsStatus`는 API 4.0부터 deprecated이고 대체는
  `guest.toolsVersionStatus2`·`guest.toolsRunningStatus`다. pyVmomi 9.1 바인딩에 여전히 존재하고 값도
  채워지므로 현행 유지하되, 상한 실측에서 값이 비면 대체 속성으로 전환한다.
  `ADAPTER_TYPE_MAP`의 VMXNET2·PCNet32(VLANCE)는 VCF 9에서 어댑터 자체가 deprecated지만
  **기존 VM에는 남아 있으므로 매핑을 지운다.**
- **vcsim으로 상한을 검증할 수 없다.** vcsim은 vSphere 6.5로 응답한다(§13.3). vcsim 통과는
  하한 쪽 증거일 뿐이며, VCF 9 대응 판정 근거가 되지 못한다.

---

## 13. 진행 현황 (2026-08-16)

Step 1 축소판(ROADMAP §7.2·§24) 기준으로 구현되었다. 아래 대조는 **이 계획서 원본과 실제
코드(`src/infrastructure/vcenter/`)를 절 단위로 비교**한 결과다 (Known Mistakes #4 방지).

### 13.1 절별 구현 대조

| 절 | 내용 | 상태 |
|---|---|---|
| §2 모듈 구성 | 7/8 구현 — `custom_fields.py`만 없음 (Step 8) | 🔶 |
| §3 세션 | `session.py` 전체 + `is_open`·`server_version` 추가 | ✅ |
| §4 PropertyCollector | 페이징·토큰 반복·`missingSet`·뷰 해제(`finally`) 전체 | ✅ |
| §5 수집 속성 | `VM_PROPERTIES_MVP` 13개 (축소판). `config.hardware.device` 의도적 제외(비용 정량화 목적). HOST~DVPG 목록 미작성 | 🔶 |
| §5.1 프로비저닝 계산 | 미구현 — Datastore 수집이 Step 6 | ⬜ |
| §5.2 Custom Attributes | 미구현 (Step 8) | ⬜ |
| §6.1 VM 매핑 | Step 1 필드만 — disks·adapters·snapshots·boot_time·created_at·annotation·custom_attributes·folder_path·resource_pool·firmware·hardware_version·cores_per_socket 제외 | 🔶 |
| §6.2 게스트 매핑 | Tools 4분기 + 상태·값 이중 확인 구현. IP(`guest.net`)·`tools_version` 제외 | 🔶 |
| §6.3 장치 매핑 | 미구현 (Step 4) | ⬜ |
| §6.4 참조 해석 후처리 | 미구현 (Step 6) — `moref_or_none`이 MoRef 원문을 저장 | ⬜ |
| §6.5 Thin 실제 사용량 | 미구현 — 계획대로 `[TODO]` 유지 | ⬜ |
| §7 연결 테스트 | 4단계(StageRunner) 구현 | 🔶 |
| §8 예외 변환 | 전체 구현 + 보강 (§13.2) | ✅ |
| §9 Reader | `list_virtual_machines`만. `_collect` 제네릭 헬퍼는 `list_*`가 1개뿐이라 미도입 | 🔶 |

### 13.2 계획 대비 편차

- **`connectionPoolTimeout=-1` 미전달** (§3의 `[검증 필요]` 항목): `SmartConnect` 기본값을 쓴다.
  필요 여부는 Step 2 장기 수집 실측에서 판단한다.
- **페이지 크기는 `Settings.collection_page_size`(기본 500) 주입** — 계획의 하드코딩 대신 설정화.
- **§7 AUTHORIZED 프로브가 VM 1종만**: 계획은 4유형(VM·Host·Datastore·Network)이지만 Step 1 수집
  대상이 VM뿐이라 맞췄다. 나머지 유형은 해당 `list_*` 추가 시(Step 6) 함께 확장한다.
- **§7 TLS_VALID 단계는 별도 핸드셰이크를 하지 않는다**: 실제 검증은 다음 단계 `SmartConnect`가
  수행한다. 별도 핸드셰이크는 세션을 두 번 여는 셈이라 vCenter 부하만 늘린다.
- **`errors.py` 보강**: 이미 도메인 예외면 통과시키는 분기, `secrets` 마스킹 파라미터 추가.
  `ssl.SSLCertVerificationError`는 `ssl.SSLError`의 하위 타입이라 분기 하나로 처리.
- **`reader.py`에 `connection_id`·`is_session_closed`·`get_outcomes` 추가** — 계획 03 Protocol 요구사항.
- **§4 헬퍼 명칭**: `_moref_id`/`_props_to_dict` 대신 공개 함수 `moref_id`/`props_to_dict` — 단위
  테스트가 직접 호출한다.

### 13.3 검증 결과 (2026-08-16 확인)

- `python scripts/arch_check.py --ci` 통과 — 위반 0 (hyperv 미참조, 읽기 전용 메서드만)
- 단위 테스트 13종 통과 — `test_vcenter_collector.py` 4종(토큰 반복 전량 수집, 오류 시 뷰 해제,
  `missingSet`→`None`, 빈 propSet) + `test_vcenter_mapper.py` 9종(핵심 필드, `instanceUuid`→MoRef
  폴백, UNKNOWN 전원, Tools 4분기, `os_source` 폴백 등)
- Protocol 준수 계약: 세 어댑터 파라미터화 테스트에 `VCenterInventoryReader` 포함 통과
  (`tests/unit/test_hyperv_readers.py`)
- 쓰기 API·HTTP 클라이언트 grep: 호출·import 0건
- **vcsim 관통 검증 (2026-08-14)**: 연결 등록 → 수집 → VM 목록 조회까지 실서버로 확인.
  vSphere 6.5로 보고되며, 게스트 정보는 `tools_not_installed`로 표시 — vcsim에 게스트 도구가
  없으므로 정상 (CLAUDE.md 개발 환경 절 참조)

### 13.4 잔여 작업

| 시점 | 작업 |
|---|---|
| **Step 2 (다음)** | 실 vCenter 실측 — §5 속성 경로 존재 여부, `instanceUuid` 공백 VM, `maxObjects` 값별 응답 시간 (ROADMAP §15) → `docs/04_field_validation.md` 기록 |
| **Step 2 (다음)** | **상한 대응 — §14.3의 7개 실측 항목.** 대상 환경에 VCF 9.x가 있으면 **§3.2 인증 확인이 최우선**이다 (연결 등록 자체가 막힐 수 있음) |
| ~~**Step 2 이전**~~ | ~~`pyproject.toml` `pyvmomi` 버전 핀~~ — ✅ **완료 (2026-08-19)**. TLS 하한(§3.1)도 함께 적용 |
| Step 4 | §6.3 장치 매핑(**opaque 백킹 3분기 포함**), §6.5 재검토, `config.hardware.device` 추가 + 수집 시간 전후 비교 |
| Step 6 | §6.4 MoRef→이름 해석, 나머지 `list_*` + AUTHORIZED 프로브 4유형 확장, **§5.3 Network/DVPG 중복 제거** |
| Step 8 | §5.2 `custom_fields.py` (FR-606) |

---

## 14. 버전 호환성 (2026-08-19 신설)

이 계획서가 **하한(6.5)만 다루고 상한(VCF 9.x)을 다루지 않았던 것**을 보완한다.
근거 확인은 Broadcom 공식 문서 + **이 개발 환경에 설치된 pyVmomi 9.1.0.0 바인딩 직접 조회**로 했다.

### 14.1 호환성 매트릭스

| vCenter | pyVmomi | 상태 | 근거 |
|---|---|---|---|
| 6.5 (하한) | 9.1.0.0 | **미검증 — 위험** | Broadcom 정책은 "직전 4개 릴리스" 지원. 6.5는 범위 밖. 단 `vim25` 협상 목록에 `version10`(6.5)이 남아 있어 동작 가능성은 있음 |
| 7.0 | 9.1.0.0 | 미검증 | 정책 범위 내 |
| 8.0 | 9.1.0.0 | 미검증 | 정책 범위 내 |
| **9.0 (VCF 9, 상한)** | 9.1.0.0 | **미검증 — §14.3 필수** | 인증·NSX·vSAN 변화 있음 |
| vcsim | 9.1.0.0 | ✅ 통과 (2026-08-14) | **6.5로 응답** — 하한 쪽 증거일 뿐 |

### 14.2 pyVmomi 의존성

**현재 `pyproject.toml`은 `"pyvmomi"` 무핀이고, 이 환경에는 9.1.0.0이 설치되어 있다.**
`docs/00_research_notes.md` §11-9가 "버전 고정" 결론을 냈는데 이 계획서와 pyproject에 반영되지 않았다.

| 항목 | 확인 결과 (pyVmomi 9.1.0.0) |
|---|---|
| PyPI 최신 | 9.1.0.0 |
| 호환 정책 | **직전 4개 vSphere 릴리스** — 6.5 보증 없음 |
| 배포 경로 | **VCF 9.0부터 독립 SDK 배포 중단, VCF Python SDK에 통합.** PyPI에는 9.1.0.0까지 게시되어 있으나 향후 공급 경로를 주시한다 |
| Python | 3.10+ (VCF SDK 문서는 3.9~3.13 표기) |
| `SmartConnect` 시그니처 | `connectionPoolTimeout`(기본 900) **유지**, `version` → **`preferredApiVersions`로 변경**, `token`·`tokenType`·`sessionId` 존재, `b64token`·`mechanism` deprecated, `disableSslCertValidation`·`serverPemCert` 존재 |
| 9.x 파괴적 변경 | `pyVmomi.Feature` 제거, `pyVmomiSettings`(`legacyThumbprintException`·`binaryIsBytearray`) 제거, `ThumbprintMismatchException` → `pyVmomi.Security.` 이동, `publicVersions`/`dottedVersions` → `ltsVersions` |

**파괴적 변경 4건은 이 계획서가 쓰지 않는 API라 직접 피해가 없다.** 그래도 무핀 상태는 위험하다 —
다음 메이저에서 무엇이 더 사라질지 모르고, 하한 6.5를 유지하는 한 상한 라이브러리와 계속 어긋난다.

**조치**: ✅ **적용 완료 (2026-08-19)** — `pyproject.toml`이 `"pyvmomi>=8.0.3,<10"`이다.
상한 확정(§14.4) 후 하한을 재조정한다.

### 14.3 Step 2 상한 실측 항목

ROADMAP §15에 등재한다. **대상 환경에 VCF 9.x가 없으면 1번만 확인하고 나머지는 보류한다.**

| # | 확인할 것 | 실패 시 영향 |
|---|---|---|
| 1 | **대상 vCenter 버전 분포의 상한** | 9.x가 있으면 2~7 전부 필수 |
| 2 | **수집 계정이 로컬 SSO 계정인가** (§3.2) | 페더레이션 계정이면 **연결 등록 자체가 불가** — 토큰 인증 착수 판단 |
| 3 | TLS 프로파일 (`COMPATIBLE` / `NIST_2024_TLS_13_ONLY`) | 후자면 클라이언트 TLS 1.3 필수 |
| 4 | `vim.Network` 뷰의 **MoRef 접두사 분포** (§5.3) | 중복 제거 규칙 확정 |
| 5 | NSX 세그먼트의 `config.backingType` 실값 (§5.3) | NSX 구분 가능 여부 |
| 6 | vSAN 데이터스토어의 `uncommitted` 유무 (§5.1) | 오버커밋 계산 가능 여부 |
| 7 | vCLS·Supervisor·템플릿 VM 비율 (§5.4) | 목록 기본 필터·리포트 집계 왜곡 정도 |

### 14.4 미해결 결정 — **사용자 판단 필요**

**`spec.md` CST-10은 하한(6.5)만 정의한다.** 상한을 요건에 못 박으려면 `spec.md` 개정이 필요하고,
이는 기존 결정 변경이므로 임의로 진행하지 않는다 (CLAUDE.md "의사결정 기록" 절).

이 계획서는 그때까지 **상한을 vCenter 9.0(VCF 9.x)으로 가정**하고 설계한다 (D-020).
ROADMAP §16-3이 이미 "Step 2 산출물로 CST-10 버전 확정"을 예정하고 있으므로,
**실측 결과와 함께 하한·상한을 동시에 확정**하는 것이 자연스럽다.

### 14.5 확인 출처

- [VCF SDKs, APIs, and CLIs — Product Support Notes (VCF 9.0)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/vcf-sdks-apis-and-clis-product-support-notes.html)
- [Product Support Notes — vSphere (VCF 9.0)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/product-support-notes-vsphere.html) — 인증 변경·deprecated 목록
- [vSphere Web Services API (VCF 9.0)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/administration-sdks-cli-and-tools/about-vmware-cloud-foundation-development/core-vsphere-apis/web-services-vim-api.html) — SOAP 유지 확인
- [Managing NSX Distributed Virtual Port Groups](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/host-switches/managing-nsx-on-a-vsphere-distributed-switch.html)
- [Security in VMware Cloud Foundation 9.0](https://blogs.vmware.com/cloud-foundation/2025/08/05/security-vmware-cloud-foundation-9-0/) — TLS 프로파일
- [ESX to ESXi and Back Again](https://vninja.net/2025/06/18/esx-to-esxi-and-back-again/) — 제품명 환원
- [pyvmomi CHANGELOG](https://github.com/vmware/pyvmomi/blob/master/CHANGELOG.md)
