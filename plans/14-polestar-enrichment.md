# 14. 폴스타 연동 — 게스트 관점 자원 정보 보강

> 작성일: 2026-08-14
> 근거: `spec.md` §2.2·FR-304·FR-601·602·606, `docs/02_decision.md` D-005·D-006·D-007·D-013,
> `plans/ROADMAP.md` §19(Step 4)·§22(Step 7), collectorinfra `schema/polestar-schema.md`·`config/db_profiles/polestar*.yaml`
> 관련 결정: **D-019** (이 계획서의 근거 결정)

## 1. 목적

포탈은 **vCenter·Hyper-V 호스트/클러스터·SCVMM** 세 경로에서 **하이퍼바이저 관점**의 VM 정보를
수집한다. 여기에는 구조적 한계가 있다 — 게스트 도구(VMware Tools / 통합 서비스)가 없으면 실제
OS·IP·호스트명을 알 수 없고(FR-501), 도구가 있어도 커널 버전·에이전트 설치 여부 같은
**게스트 내부 사실**과 조직이 부여한 **운영 정보**는 얻지 못한다.

폴스타는 같은 서버를 **게스트 OS 관점**(에이전트 기반)에서 이미 수집하고 있다.
이 계획은 **폴스타에서 자원 정보를 조회해, 하이퍼바이저 3경로에서 수집한 VM과 매칭시켜
자원 정보를 보완하는 것**이다.

| 이 계획으로 얻는 것 | 근거 |
|---|---|
| VMware Tools 미설치 VM의 실제 OS·IP·호스트명 (폴스타 에이전트가 대신 보고) | FR-501의 "수집 불가"를 일부 해소 |
| 커널/패치 레벨, 벤더·모델·시리얼, 에이전트 설치 여부 | spec §2.2에 없는 게스트 내부 속성 |
| 폴스타의 조직 정보(`group_path`·`location`·`manager_zone`·`importance_id`·`description`, 가능하면 담당자)를 포탈 메타데이터 **초기 제안값**으로 | FR-601·606 |
| 하이퍼바이저 값과 게스트 값의 **불일치 탐지** (예: vCPU 4인데 게스트는 2코어 인식) | FR-304 속성 출처 우선순위의 실증 |

**매칭 대상은 하이퍼바이저 종류를 가리지 않는다.** vCenter VM, Hyper-V 호스트/클러스터 VM,
SCVMM VM 모두 동일한 매칭 규칙을 적용한다 (§6.1.1). 유스케이스에 하이퍼바이저 분기를 두지
않는다는 원칙(D-003)이 여기에도 적용된다.

### 1.1 범위 (사용자 결정 2026-08-14)

**포함**
1. **게스트 OS 상세 보강** — 폴스타 서버 사실을 수집·저장하고 VM에 링크하여 조회
2. **조직 메타데이터 초기값 확보** — 폴스타의 조직 정보를 포탈 메타데이터 제안값으로 제공

**제외 (명시적 범위 밖 — §16)**
- 모니터링 등록 갭 분석 화면
- 물리 서버 인벤토리 확장
- **폴스타 성능 지표(`cmm_metric_stat_*`)·알람(`cmm_alarm*`) 수집** — `spec.md` §1.2 비목표
- 폴스타에 대한 쓰기 일체

> **`cmm_metric_stat_h/d/m`을 건드리지 않는다.** 폴스타 DB에 CPU/메모리 사용률 통계가 있고
> 조인 한 줄이면 붙지만, `spec.md` §1.2는 "성능 모니터링 및 실시간 알람"을 **비목표**로 못박았다.
> 이 포탈은 인벤토리 CMDB이지 모니터링 대시보드가 아니다. 요청이 오면 범위 확인부터 한다.

## 2. 폴스타는 무엇인가 — 데이터 모델

폴스타는 사내 인프라 모니터링·자산 시스템이며, **사이트별로 독립 인스턴스**가 운영된다
(collectorinfra `mcp_server/config.toml` 기준: `polestar`, `polestar_b0`(은행 레거시·K리전 은행존),
`polestar_cm_gp`(K리전 공동존 김포 운영/DR), `polestar_cm_yd`(공동존 여의도 개발/스테이징), `polestar_pg`).
**DB 엔진이 인스턴스마다 다르다 — PostgreSQL 또는 IBM DB2.**

### 2.1 테이블군 — 인벤토리는 2개지만 DB는 그보다 크다

아래는 collectorinfra가 **실제로 조회하는** 테이블이다. 폴스타 제품 DB에는 이보다 많은 테이블이
있으며, collectorinfra는 `allowed_tables`로 조회 범위를 좁혀 쓰고 있다
(`config/db_profiles/polestar*.yaml`). **전체 목록은 착수 시 직접 확인한다** (§2.5).

| 군 | 테이블 | 이 계획에서의 취급 |
|---|---|---|
| **인벤토리 (핵심)** | `cmm_resource`, `core_config_prop` | **수집 대상** |
| **성능 통계** | `cmm_metric_stat_h`, `cmm_metric_stat_d`, `cmm_metric_stat_m` | **범위 밖** — `spec.md` §1.2 비목표 |
| **알람** | `cmm_alarm`, `cmm_alarm_active`, `cmm_alarm_def`, `cmm_alarm_log`, `cmm_alarm_def_noti`, `..._noti_user`, `..._noti_group`, `..._noti_role`, `..._noti_rmtype` | **범위 밖** — 동일 |
| **계정·ACL·담당자** | `acc_role`, `acc_user_group`, `acc_acl_resource_manager_type` | **조건부** — 담당자 메타데이터 제안에 쓸 수 있다 (§2.4) |
| **레거시 lookup** | `cmm_vendor`, `cmm_os`, `cmm_os_param` | **조회 금지** — 데이터가 EAV와 불일치할 수 있다 (§2.3) |

인벤토리 조인은 **`cmm_resource.resource_conf_id = core_config_prop.configuration_id`** 하나다.
`cmm_resource.id = configuration_id` 직접 조인은 **금지**다 (collectorinfra 프로필의 명시 규칙).

### 2.2 서버 1대에서 얻을 수 있는 값

**(a) `cmm_resource` 서버 행의 컬럼** (전 59컬럼 중 이 계획이 쓰는 것)

| 컬럼 | 내용 | 포탈에서의 쓰임 |
|---|---|---|
| `id` | PK (BIGINT) | `native_id`, 키셋 페이징 커서 |
| `name` | 리소스명 / **공동존은 장비 식별명** (§2.3) | 매칭 후보, 표시 |
| `hostname` | OS 호스트명 | 매칭 **규칙 3** |
| `ipaddress` | 대표 IP | 매칭 4순위 |
| **`uuid`** | VARCHAR(255) — **용도 미확인** | 매칭 **규칙 1(UUID 계열)** 후보. **§15-4 최우선 확인** |
| `resource_key` | 리소스 고유 키 (UUID 등) | 매칭 보조 후보 |
| `identifier` | 식별자 (VARCHAR 4000) | 매칭 보조 후보 |
| `location` | 물리적 위치 | **메타데이터 제안** |
| `group_path` | 그룹 경로 | **메타데이터 제안** (조직·서비스 추정) |
| `manager_zone` | 관리 존 | **메타데이터 제안** |
| `importance_id` | 중요도 ID | **메타데이터 제안** — 포탈의 중요도(Tier)에 대응 |
| `description` | 설명 (한국어) | **메타데이터 제안** |
| `acl_manager_id`, `acl_manager_group_id`, `acl_id` | 담당자·담당 그룹·ACL | **담당자 제안** (§2.4) |
| `avail_status` | 0=정상, 그 외=비정상 | 폴스타 관측 상태 표시 |
| `id_ancestry`, `parent_resource_id`, `platform_resource_id` | 계층 관계 | 하위 리소스(NIC 등) 수집 |
| `ctime`, `mtime`, `dtime` | 생성·수정·**삭제** 시각 (epoch ms) | `dtime IS NULL`이 유효 행 조건 |
| `resource_type` | 리소스 유형 | 서버 행 선별 |

**(b) EAV(`core_config_prop`) 속성** — `resource_type`별로 그룹이 다르다

| resource_type | 속성 | 포탈에서의 쓰임 |
|---|---|---|
| `server.Server` / `platform.server` | `SerialNumber`, `Model`, `Vendor`, `OSType`, `OSVerson`(제품 오탈자), `PatchLevel`, `Hostname`, `IPaddress`, `AgentID`, `AgentVersion`, `InstallPath`, `GMT` | 매칭 **규칙 1(시리얼 → UUID)** + 게스트 사실 |
| `server.Cpus` | `MODEL`, `LOGICALCORE`, `PHYSICALCORE`, `PHYSICALCPU`, `SPEED`, `HYPERTHREADING` | 하이퍼바이저 vCPU와 대조 |
| `server.Memory` | `TotalSize` (예: `62.1 GB`) | 할당 메모리와 대조 |
| `server.VirtualMemory` | `SwapTotalSize`, `TotalPageFileSize` | 게스트 사실 |
| `server.Disks` | `DiskCount`, `TotalSize` | 게스트 사실 |
| `server.NetworkInterface` | `MAC`, `IP`, `NETMASK`, `STATUS`, `BANDW`, `MTU` | 매칭 **규칙 2(MAC)**·**규칙 4(IP)** |
| `server.Server` | `OSParameter` (LOB) | **수집하지 않는다** — 커널 파라미터 전문은 인벤토리 속성이 아니다 |

**(c) 계층 하위 리소스** — `server.FileSystem`, `server.Cpu`(개별 코어), `server.Hba`, `server.Process`,
`server.LogMonitor`, `server.ProcessMonitor` 등. **이번 범위에서는 NIC만 수집한다** (매칭 키).

> **가상머신은 폴스타에서 "서버"로만 보인다.** 폴스타에는 VM 전용 `resource_type`이 없다.
> 다만 `Model = "VMware Virtual Platform"`, `Vendor = "VMware, Inc."`, `SerialNumber = "VMware-…"`
> 같은 값으로 가상화 여부를 **추정**할 수 있다. 이 추정은 매칭 후보를 좁히는 힌트로만 쓰고,
> 판정 근거로 삼지 않는다.

### 2.3 조회하지 않는 테이블 — 레거시 lookup

`cmm_resource`에 `vendor_id`·`os_id`·`os_param_id` 컬럼이 있어 `cmm_vendor`·`cmm_os`·`cmm_os_param`과
FK 관계처럼 보이지만, **실 운영에서 쓰지 않는 레거시 테이블이다.** 벤더·OS·OS파라미터는 전부
`core_config_prop` EAV에 있으며, **lookup 테이블의 데이터는 EAV와 불일치할 수 있다.**
collectorinfra는 이 조인을 3중 방어로 차단하고 있다 (Plan 42 / D-028).

포탈도 **이 세 테이블을 조회하지 않는다.** `queries.py`가 만드는 SQL에 등장할 이유가 없으며,
테스트로 고정한다 (§14).

### 2.4 담당자 정보 — `acc_*` 조인은 확인 후 결정

`cmm_resource.acl_manager_id`·`acl_manager_group_id`와 `acc_role`·`acc_user_group`·
`acc_acl_resource_manager_type`을 조인하면 **서버별 담당자·담당 그룹**을 얻을 수 있을 가능성이 있다.
목적 ②(조직 메타데이터 초기값)에 가장 직접적으로 맞는 데이터다.

**그러나 collectorinfra에도 이 테이블들의 구조와 조인 경로가 문서화되어 있지 않다.**
알람 통보 대상 조회용으로 `alarm_allowed_tables`에 허용만 되어 있을 뿐이다.

| 단계 | 내용 |
|---|---|
| 1 | 착수 시 `get_table_schema`로 `acc_*` 3개 테이블 구조를 확인한다 (§2.5) |
| 2 | `acl_manager_id` → 사용자명 경로가 성립하면 **9-B 메타데이터 제안**에 담당자를 포함한다 |
| 3 | 성립하지 않으면 `group_path`·`manager_zone`·`description`만으로 제안한다 |

**개인정보 주의** — 담당자명·조직명은 인벤토리 정보보다 민감도가 높다.
가져오더라도 **제안값 표시까지만** 하고, 별도 테이블에 원본을 축적하지 않는다.
가져온 값은 `external_servers.raw_facts`가 아니라 제안 응답에만 싣는다.

### 2.5 착수 시 스키마 탐색 절차 (건너뛰지 않는다)

이 계획은 collectorinfra의 조회 범위를 근거로 작성되었다. **실제 폴스타 DB에는 더 많은 테이블이
있으며, 사이트마다 다를 수 있다.** 9-1 단계에서 다음을 수행하고 결과를 기록한다.

```
1. list_sources               → 연동 가능한 폴스타 인스턴스와 DB 엔진 확인
2. search_objects(source, "cmm_*")  → 인벤토리·알람 계열 실제 테이블 목록
   search_objects(source, "acc_*")  → 계정·ACL 계열
   search_objects(source, "*")      → 전체 목록 (사이트별 차이 확인)
3. get_table_schema(source, "cmm_resource")       → 컬럼·PK 확인 (특히 uuid·identifier)
   get_table_schema(source, "core_config_prop")
   get_table_schema(source, "acc_acl_resource_manager_type")  → §2.4 판단
4. 결과를 docs/04_field_validation.md에 기록하고, 이 계획서 §2.1 표를 갱신한다
```

**탐색 결과가 이 계획서와 다르면 계획서를 고친다.** 코드를 먼저 맞추지 않는다.

### 2.6 사이트별 차이 — 매칭 설계에 직접 영향

**공동존 폴스타(`polestar_cm_gp`·`polestar_cm_yd`)는 `name`과 `hostname`이 서로 다른 값이다.**

| 컬럼 | 공동존 | 그 외 |
|---|---|---|
| `cmm_resource.name` | 폴스타에 등록된 **장비 식별명** (예: `cocm-hdkapp01`) | 리소스명 |
| `cmm_resource.hostname` | OS가 인식하는 **실제 호스트명** | 호스트명 |

collectorinfra는 이 차이를 몰라 잘못된 결과를 낸 이력이 있다 (프로필 `[filter_conditions 필드명 재매핑]` 주석).
**포탈은 `name`과 `hostname`을 둘 다 매칭 후보로 쓰고, 어느 쪽이 맞았는지 `evidence`에 기록한다** (§6.1).

## 3. 왜 `HypervisorInventoryReader`가 아닌가

폴스타 어댑터를 기존 커넥터 Protocol에 끼우고 싶은 유혹이 있다. **하지 않는다.**

| 이유 | 설명 |
|---|---|
| 폴스타는 하이퍼바이저가 아니다 | VM을 소유하지 않는다. `list_virtual_machines()`가 반환할 것이 없다 |
| `VirtualMachine.connection_id`는 하이퍼바이저 연결이다 | 폴스타를 `connections`에 넣으면 VM의 소속이 모호해지고 `AccessScope`(연결 단위 권한)가 무너진다 |
| `ConnectionKind`에 값을 추가하면 리더 팩토리 분기가 필요하다 | Known Mistakes 5번 — enum에 미구현 값을 남기지 않는다 |
| 수집 단위가 다르다 | 하이퍼바이저는 VM/호스트/데이터스토어, 폴스타는 "서버 사실" 하나다 |

**별도 Protocol `ServerFactReader`를 `src/domain/ports.py`에 정의한다.**
`ports.py`는 `scripts/arch_check.py`의 읽기 전용 검사 대상이므로, 자원 변경 접두사가 자동으로 차단된다.

## 4. 모듈 구성

```
src/domain/
  external.py                    # ExternalSource, ExternalServerFact, ServerLink, MatchRule …
  ports.py                       # ServerFactReader Protocol 추가 (읽기 전용 검사 대상)
src/infrastructure/polestar/
  __init__.py                    # PolestarFactReader만 공개
  mcp_client.py                  # DBHub MCP(SSE) 클라이언트 — execute_sql / health_check
  queries.py                     # 폴스타 SQL 생성 (키셋 페이징, 방언 분기)
  reader.py                      # PolestarFactReader (ServerFactReader 구현)
  mapper.py                      # 폴스타 행 → ExternalServerFact 정규화
  errors.py                      # MCP/DB 오류 → 도메인 예외
src/infrastructure/repository/
  external_repo.py               # ExternalSourceRepository / ExternalServerRepository / ServerLinkRepository
src/application/
  external_sync_service.py       # 동기화 유스케이스 (FactReaderFactory 주입)
  server_match_service.py        # 매칭 엔진 (순수 로직 — I/O 없음)
src/api/routes/external_sources.py
src/api/schemas/external.py
static/external-sources.html     # 관리자 — 소스 관리
static/match-candidates.html     # 운영자 — 매칭 후보 확정
migrations/versions/000X_polestar_enrichment.py
```

### 4.1 `scripts/arch_check.py` 변경 (필수)

새 패키지를 등록하지 않으면 **검사에서 조용히 제외된다** (계획 README §3.1).

```python
MODULE_LAYER_MAP = {
    ...
    "src.infrastructure.polestar":  "infrastructure",   # DBHub MCP 경유 폴스타 조회 어댑터
}

# 기존 HYPERVISOR_ADAPTERS는 유지하고, 격리 대상 어댑터 목록을 확장한다.
EXTERNAL_ADAPTERS: tuple[str, ...] = ("src.infrastructure.polestar",)
ISOLATED_ADAPTERS = HYPERVISOR_ADAPTERS + EXTERNAL_ADAPTERS
```

특화 규칙 3개에 다음을 반영한다:

1. `application`/`orchestration` → `src.infrastructure.polestar` **직접 import 금지** (Protocol 주입)
2. 어댑터 간 교차 참조 금지에 **polestar ↔ vcenter/hyperv** 추가
3. 읽기 전용 접두사 검사는 `ServerFactReader`가 `ports.py`에 있으므로 자동 적용

## 5. 접근 경로 — DBHub MCP 경유 (사용자 결정)

collectorinfra가 운영 중인 **DBHub MCP 서버**(`mcp_server/`, SSE, 기본 `:9099`)에 붙어
`execute_sql` 도구로 SELECT를 실행한다. 폴스타 DB 자격증명과 드라이버(DB2 `ibm_db` 포함)는
MCP 서버가 관리하므로 **포탈은 DB 자격증명을 보관하지 않는다.**

```
포탈 수집 워커 ──SSE──► DBHub MCP 서버 ──asyncpg/ibm_db──► 폴스타 DB (읽기 전용 계정)
     │                        │
     │                        └─ validate_readonly()로 DML/DDL 차단 (서버측 가드)
     └─ 포탈측 SELECT 전용 가드 (이중 방어)
```

### 5.1 MCP 도구 계약 (collectorinfra `mcp_server/tools.py` 기준)

| 도구 | 인자 | 응답(JSON 문자열) |
|---|---|---|
| `execute_sql` | `source`, `sql` | `{columns, rows, row_count, truncated, execution_time_ms}` 또는 `{error}` |
| `health_check` | `source` | `{source, status: healthy\|unhealthy\|not_found, response_time_ms, source_type}` |
| `list_sources` | — | `[{name, type, readonly, query_timeout, max_rows}]` |

`source`는 `mcp_server/config.toml`의 `[[sources]] name`과 **정확히 일치**해야 한다.
불일치하면 "알 수 없는 소스" 오류가 난다.

### 5.2 반드시 지킬 5가지

1. **SQL에 사용자 입력을 넣지 않는다.**
   `execute_sql`은 SQL 문자열만 받고 **파라미터 바인딩이 없다.** 이 계획의 조회는 전량 동기화이므로
   사용자 입력이 개입할 이유가 없다. 유일한 가변값인 페이징 커서는 `int()`로 강제 캐스팅한다.
   화면에서 폴스타를 임의 조회하는 기능은 만들지 않는다.

2. **`truncated: true`를 무시하지 않는다.**
   서버 `max_rows`(기본 10,000)를 넘으면 조용히 잘린다. 잘린 것을 못 보면 **인벤토리에 구멍이 생기고
   그 서버들은 "폴스타 미등록"으로 오표시된다.** `truncated`면 페이지 크기를 낮춰 재조회하고,
   그래도 발생하면 동기화를 **실패로 처리**한다.

3. **`error` 키를 성공으로 읽지 않는다.**
   MCP 도구는 실패도 200 + `{"error": ...}` 형태로 돌려준다. 파싱 후 `error` 키를 먼저 검사한다.

4. **MCP 세션은 하나의 태스크 안에서 열고 닫는다.**
   SSE 클라이언트는 `__aenter__`/`__aexit__`를 수동 관리하며, 다른 태스크에서 닫으면
   anyio 취소 스코프 오류가 난다. 동기화 1회 = 세션 1개로 묶는다.

5. **포탈측에도 SELECT 전용 가드를 둔다.**
   서버가 막아 주더라도, 포탈이 보낸 SQL이 SELECT임을 스스로 검증한다 (계획 10의 이중 방어 원칙).
   가드는 `queries.py`에서 생성된 SQL에만 적용되므로 단순 접두사 검사로 충분하다.

### 5.3 가용성 종속을 조회 경로로 번지게 하지 않는다

**MCP 서버가 죽으면 동기화만 멈춘다. 포탈 조회는 영향받지 않는다.**
이것이 §7의 스냅샷 저장이 필수인 이유다 — 조회 시점에 폴스타를 조인하면 외부 시스템 장애가
곧바로 포탈 화면 장애가 된다. 이는 "조회는 저장소 경유" 제약(D-007) 위반이기도 하다.

### 5.4 `mcp_client.py` 골격

```python
class DbHubMcpClient:
    """DBHub MCP 서버(SSE) 클라이언트. 읽기 전용 SELECT만 보낸다."""

    def __init__(self, server_url: str, timeout_s: int = 60) -> None: ...

    async def __aenter__(self) -> "DbHubMcpClient":
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        self._sse_ctx = sse_client(url=self._server_url)
        read, write = await self._sse_ctx.__aenter__()
        self._session_ctx = ClientSession(read, write)
        self._session = await self._session_ctx.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None: ...   # 역순 정리, 예외 삼키지 않음

    async def fetch_rows(self, source: str, sql: str) -> list[dict[str, Any]]:
        """SELECT를 실행하고 행 목록을 반환한다.

        Raises:
            ExternalSourceError: MCP 오류, error 응답, truncated 응답
        """
        _assert_select_only(sql)                      # 포탈측 가드
        raw = await self._call_tool("execute_sql", {"source": source, "sql": sql})
        payload = json.loads(raw)
        if "error" in payload:
            raise ExternalSourceError(payload["error"])
        if payload.get("truncated"):
            raise ResultTruncatedError(payload.get("row_count", 0))
        return payload.get("rows", [])

    async def check(self, source: str) -> bool: ...   # health_check 도구
```

## 6. 매칭 — 이 계획의 핵심 위험 지점

**오매칭은 CMDB의 신뢰를 통째로 깨뜨린다.** 잘못 붙은 소유자·OS 정보는 사람이 발견하기 전까지
정답처럼 보인다. 따라서 규칙별로 **자동 확정과 후보 제시를 나눈다.**

### 6.1 매칭 규칙과 신뢰 등급

| 규칙 | 이름 | 폴스타 측 | 포탈 측 | 처리 |
|---|---|---|---|---|
| 1 | **UUID 계열** | ① `cmm_resource.uuid` [용도 확인 후 — §15-4] ② EAV `SerialNumber` 정규화 ③ `resource_key`·`identifier` (①②가 모두 비었을 때만) | `virtual_machines.bios_uuid` 정규화 | **자동 확정** |
| 2 | **MAC** | EAV `server.NetworkInterface.MAC` | `vm_adapters.mac` (Step 4) | **자동 확정** (1:1일 때만) |
| 3 | 호스트명 | `hostname`, EAV `Hostname`, `name` | `guest_hostname`(FQDN 앞부분), `vm.name` | **후보** → 운영자 확정 |
| 4 | IP | `ipaddress`, NIC EAV `IP` | `vm_adapter_ips` (Step 4) | **후보만. 자동 확정 금지** |

**규칙 1은 출처가 여럿이므로 어느 값이 맞았는지 `evidence`에 반드시 기록한다.**
`{"rule": 1, "polestar_field": "serial_number", "value": "421c63…"}` 형태로 남기지 않으면
오매칭 조사 때 어느 경로가 틀렸는지 알 수 없다.

### 6.1.1 포탈 측 `bios_uuid`의 출처 — 하이퍼바이저 3경로 모두 대응한다

| 연결 종류 | `bios_uuid` 출처 | 비고 |
|---|---|---|
| vCenter | `config.uuid` | Step 1부터 저장 중 (`virtual_machines.bios_uuid`) |
| Hyper-V 호스트·클러스터 (경로 A) | `Msvm_VirtualSystemSettingData.BIOSGUID` | 계획 05 §8.3 |
| SCVMM (경로 B) | VM의 `BiosGuid` | 계획 05 §8.4 — `native_id`는 VM GUID이며 BIOS UUID와 **다른 값**이다. 혼동 금지 |

**Hyper-V 게스트의 SMBIOS 시리얼이 BIOSGUID와 대응되는지는 확인되지 않았다** (§15-5).
Windows Hyper-V는 시스템 시리얼을 BIOS GUID와 별도로 노출할 수 있다.
대응하지 않으면 Hyper-V/SCVMM VM은 규칙 2·3으로만 매칭되며, 이는 **정상 동작**이다 —
매칭률이 낮게 나오는 것을 결함으로 오해하지 않도록 화면에 매칭 규칙을 표시한다.

> **규칙 3·4를 자동 확정하지 않는 이유.** 호스트명은 클론 VM·템플릿에서 중복되고, IP는 DHCP로
> 바뀌며 VLAN이 다르면 같은 IP가 여럿 존재한다. 이 두 규칙으로 자동 확정하면 **틀린 링크가
> 조용히 쌓인다.** 후보로 제시하고 사람이 확정한다.

### 6.2 정규화 규칙 — 값을 비교하기 전에 반드시 통과시킨다

```python
def normalize_uuid(value: str | None) -> str | None:
    """BIOS UUID / 시리얼을 32자리 소문자 hex로 정규화한다.

    VMware 게스트의 SMBIOS 시리얼은 'VMware-42 1c 63 ... ' 형태로,
    접두사와 구분자를 제거하면 BIOS UUID와 같은 32 hex가 된다. [검증 필요 — §15]
    """
    if not value:
        return None
    text = value.strip()
    for prefix in ("VMware-", "vmware-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = re.sub(r"[^0-9a-fA-F]", "", text).lower()
    return text if len(text) == 32 else None


def resolve_uuid(fact_row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """규칙 1 키를 결정한다. (정규화된 32 hex, 출처 컬럼명)을 반환한다.

    출처 우선순위는 §6.1의 ①②③이며, **`cmm_resource.uuid`의 용도가 확인되기 전에는
    ①을 건너뛴다** (§15-4). 확인 결과에 따라 설정 한 줄로 켠다 —
    확인 전에 켜면 정체 불명의 값으로 자동 확정이 일어난다.
    """
    for column in ("polestar_uuid", "serial_number", "resource_key", "identifier"):
        normalized = normalize_uuid(fact_row.get(column))
        if normalized:
            return normalized, column
    return None, None


def normalize_mac(value: str | None) -> str | None:
    """MAC을 구분자 없는 소문자 12 hex로 정규화한다 (b4:96:91:0e:bd:a6 → b496910ebda6)."""


def normalize_hostname(value: str | None) -> str | None:
    """소문자화 + 도메인 제거. 'srv01.corp.local' → 'srv01'."""
```

**정규화 결과가 규격을 벗어나면 그 규칙은 적용하지 않는다.** 32 hex가 아닌 시리얼(물리 서버의
`HOST123456AB` 등)로 유사도 매칭을 시도하지 않는다.

### 6.3 충돌 처리 — 1:1이 아니면 자동 확정하지 않는다

```
규칙 R로 매칭 후보 집합을 만든다
  ├─ VM 1 ↔ 폴스타 서버 1  → 규칙 1·2면 자동 확정 / 규칙 3·4면 후보 등록
  ├─ VM 1 ↔ 폴스타 서버 N  → 자동 확정 금지. 전부 후보로 등록하고 충돌 표시
  └─ VM N ↔ 폴스타 서버 1  → 동일 (클론 VM에서 흔하다)
```

이미 확정된 링크가 있는 VM/서버는 **낮은 순위 규칙이 덮어쓰지 않는다.**
상위 규칙이 새로 성립하면 승격하되, 그 사실을 감사 로그에 남긴다.

### 6.4 운영자 확정 후에는 자동 매칭이 뒤집지 않는다

`matched_by`가 사용자명인 링크는 자동 매칭의 대상에서 제외한다.
사람이 확인한 판단을 배치가 되돌리면 신뢰를 잃는다.

### 6.5 매칭 엔진은 순수 함수로 만든다

`server_match_service.py`는 **I/O를 하지 않는다.** 입력은 VM 매칭 키 목록과 폴스타 서버 키 목록,
출력은 링크·후보·충돌 목록이다. 이렇게 해야 오매칭 시나리오를 단위 테스트로 재현할 수 있다.

```python
@dataclass(frozen=True, slots=True)
class MatchOutcome:
    links: tuple[ResolvedLink, ...]         # 자동 확정 (규칙 1·2, 1:1)
    candidates: tuple[MatchCandidate, ...]  # 사람 확인 대기 (규칙 3·4 또는 충돌)
    conflicts: tuple[MatchConflict, ...]    # 1:N / N:1 감지 결과


def match_servers(
    vms: Sequence[VmMatchKeys], facts: Sequence[ExternalServerFact]
) -> MatchOutcome: ...
```

**기존 `resource_identities` 테이블을 조회 인덱스로 재사용한다.** Step 4가 rule 3(MAC+이름),
Step 5가 rule 2(BIOS UUID)를 채우므로, 폴스타 키로 이 인덱스를 역조회하면 VM 전량 로드 없이
후보를 좁힐 수 있다 (계획 06 §2.7).

## 7. DB 스키마

`spec.md` §2 자원 카탈로그의 자원이 아니므로 **`virtual_machines`에 컬럼을 추가하지 않는다.**
수집 경로와 보강 경로가 섞이면 재수집이 보강값을 덮어쓰는 사고가 난다 (FR-602).

```sql
-- 외부 사실 소스 (현재는 폴스타만)
CREATE TABLE external_sources (
    source_id           UUID PRIMARY KEY,
    kind                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    -- DBHub MCP 서버의 [[sources]] name과 정확히 일치해야 한다 (§5.1)
    mcp_source_name     TEXT NOT NULL UNIQUE,
    -- 폴스타 스키마 접두사. PostgreSQL 'polestar' / DB2 'POLESTAR'
    db_schema           TEXT NOT NULL DEFAULT 'polestar',
    -- 'postgresql' | 'db2' — 페이징 구문 분기에 쓴다 (§8.2)
    db_dialect          TEXT NOT NULL,
    enabled             BOOLEAN NOT NULL DEFAULT true,
    status              TEXT NOT NULL DEFAULT 'active',
    last_attempt_at     TIMESTAMPTZ,
    last_success_at     TIMESTAMPTZ,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_external_sources_kind    CHECK (kind IN ('polestar')),
    CONSTRAINT ck_external_sources_dialect CHECK (db_dialect IN ('postgresql', 'db2'))
);

-- 폴스타 서버 스냅샷. 조회는 항상 이 테이블을 본다 (§5.3)
CREATE TABLE external_servers (
    external_server_id  UUID PRIMARY KEY,
    source_id           UUID NOT NULL REFERENCES external_sources(source_id) ON DELETE CASCADE,
    -- cmm_resource.id (서버 행)
    native_id           TEXT NOT NULL,
    display_name        TEXT,          -- cmm_resource.name  (공동존은 장비 식별명 — §2.6)
    hostname            TEXT,          -- cmm_resource.hostname (OS 호스트명)
    primary_ip          INET,
    serial_number       TEXT,
    -- cmm_resource.uuid 원본. 용도 확인 전에는 저장만 하고 매칭에 쓰지 않는다 (§15-4)
    polestar_uuid       TEXT,
    resource_key        TEXT,
    identifier          TEXT,
    -- 위 값들에서 정규화한 32 hex. 매칭 규칙 1 키 (§6.2)
    normalized_uuid     TEXT,
    -- 어느 컬럼에서 normalized_uuid가 나왔는지. evidence 기록용 (§6.1)
    uuid_source         TEXT,
    mac_addresses       TEXT[]  NOT NULL DEFAULT '{}',   -- 정규화된 12 hex
    ip_addresses        INET[]  NOT NULL DEFAULT '{}',
    os_type             TEXT,
    os_version          TEXT,
    patch_level         TEXT,
    vendor              TEXT,
    model               TEXT,
    cpu_model           TEXT,
    cpu_logical_cores   INTEGER,
    cpu_physical_cores  INTEGER,
    cpu_sockets         INTEGER,
    memory_mb           BIGINT,
    agent_id            TEXT,
    agent_version       TEXT,
    location            TEXT,
    group_path          TEXT,
    manager_zone        TEXT,
    importance_id       INTEGER,
    description         TEXT,
    avail_status        SMALLINT,      -- 0=정상, 그 외=비정상
    -- 위에 승격하지 않은 EAV 원본 (D-013 JSONB 활용)
    raw_facts           JSONB,
    lifecycle           TEXT NOT NULL DEFAULT 'active',
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_external_server_native UNIQUE (source_id, native_id)
);

CREATE INDEX idx_ext_servers_uuid     ON external_servers (normalized_uuid) WHERE normalized_uuid IS NOT NULL;
CREATE INDEX idx_ext_servers_hostname ON external_servers (lower(hostname));
CREATE INDEX idx_ext_servers_macs     ON external_servers USING gin (mac_addresses);

-- VM ↔ 폴스타 서버 링크. VM 1개당 1건, 서버 1개당 1건 (1:1)
CREATE TABLE external_server_links (
    resource_id         UUID PRIMARY KEY
                        REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    external_server_id  UUID NOT NULL
                        REFERENCES external_servers(external_server_id) ON DELETE CASCADE,
    match_rule          SMALLINT NOT NULL,    -- 1 uuid | 2 mac | 3 hostname | 4 ip
    -- 'auto' 또는 확정한 사용자명. 사용자 확정은 자동 매칭이 뒤집지 않는다 (§6.4)
    matched_by          TEXT NOT NULL,
    matched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 어떤 값이 일치했는지. 오매칭 조사에 반드시 필요하다
    evidence            JSONB,
    CONSTRAINT uq_link_external_server UNIQUE (external_server_id),
    CONSTRAINT ck_link_rule CHECK (match_rule BETWEEN 1 AND 4)
);

-- 사람 확인 대기 후보 (규칙 3·4 또는 충돌)
CREATE TABLE external_match_candidates (
    candidate_id        BIGSERIAL PRIMARY KEY,
    resource_id         UUID NOT NULL REFERENCES virtual_machines(resource_id) ON DELETE CASCADE,
    external_server_id  UUID NOT NULL REFERENCES external_servers(external_server_id) ON DELETE CASCADE,
    match_rule          SMALLINT NOT NULL,
    conflict            BOOLEAN NOT NULL DEFAULT false,   -- 1:N / N:1 감지
    evidence            JSONB,
    status              TEXT NOT NULL DEFAULT 'pending',
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_by         TEXT,
    resolved_at         TIMESTAMPTZ,
    CONSTRAINT uq_candidate UNIQUE (resource_id, external_server_id, match_rule),
    CONSTRAINT ck_candidate_status CHECK (status IN ('pending', 'accepted', 'rejected'))
);

CREATE INDEX idx_candidates_pending ON external_match_candidates (detected_at)
    WHERE status = 'pending';
```

### 7.1 만들지 않는 테이블

| 테이블 | 이유 |
|---|---|
| `external_server_filesystems`, `..._nics` | 게스트 파일시스템·NIC 상세는 이번 범위가 아니다. 필요해지면 `raw_facts`에서 승격한다 |
| `external_metrics` | 성능 지표는 비목표 (§1.1) |
| `polestar_credentials` | **DB 자격증명은 MCP 서버가 보관한다.** 포탈이 갖지 않는다 (§5) |

### 7.2 삭제된 폴스타 서버를 지우지 않는다

폴스타에서 `dtime IS NOT NULL`이 되었거나 조회 결과에서 사라진 서버는 **삭제하지 않고**
`lifecycle = 'missing'`으로 표시한다. 부분 실패 원칙(NFR-302)과 동일하다 —
폴스타 쪽 일시 장애로 결과가 비었을 때 링크가 통째로 사라지면 복구 비용이 크다.

## 8. 조회 SQL

### 8.1 서버 + EAV 피벗 (1페이지)

```sql
SELECT
  svr.id                AS native_id,
  svr.name              AS display_name,
  svr.hostname          AS hostname,
  svr.ipaddress         AS ip_address,
  svr.uuid              AS polestar_uuid,      -- 용도 확인 전에는 저장만 (§15-4)
  svr.resource_key      AS resource_key,
  svr.identifier        AS identifier,
  svr.location          AS location,
  svr.group_path        AS group_path,
  svr.manager_zone      AS manager_zone,
  svr.importance_id     AS importance_id,
  svr.description       AS description,
  svr.avail_status      AS avail_status,
  MAX(CASE WHEN cc.name = 'SerialNumber' THEN cc.stringvalue_short END) AS serial_number,
  MAX(CASE WHEN cc.name = 'Model'        THEN cc.stringvalue_short END) AS model,
  MAX(CASE WHEN cc.name = 'Vendor'       THEN cc.stringvalue_short END) AS vendor,
  MAX(CASE WHEN cc.name = 'OSType'       THEN cc.stringvalue_short END) AS os_type,
  MAX(CASE WHEN cc.name = 'OSVerson'     THEN cc.stringvalue_short END) AS os_version,
  MAX(CASE WHEN cc.name = 'PatchLevel'   THEN cc.stringvalue_short END) AS patch_level,
  MAX(CASE WHEN cc.name = 'AgentID'      THEN cc.stringvalue_short END) AS agent_id,
  MAX(CASE WHEN cc.name = 'AgentVersion' THEN cc.stringvalue_short END) AS agent_version,
  MAX(CASE WHEN c.resource_type = 'server.Cpus'   AND cc.name = 'MODEL'         THEN cc.stringvalue_short END) AS cpu_model,
  MAX(CASE WHEN c.resource_type = 'server.Cpus'   AND cc.name = 'LOGICALCORE'   THEN cc.stringvalue_short END) AS cpu_logical_cores,
  MAX(CASE WHEN c.resource_type = 'server.Cpus'   AND cc.name = 'PHYSICALCORE'  THEN cc.stringvalue_short END) AS cpu_physical_cores,
  MAX(CASE WHEN c.resource_type = 'server.Cpus'   AND cc.name = 'PHYSICALCPU'   THEN cc.stringvalue_short END) AS cpu_sockets,
  MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize'     THEN cc.stringvalue_short END) AS memory_total
FROM {schema}.cmm_resource svr
LEFT JOIN {schema}.cmm_resource c
       ON (c.platform_resource_id = svr.id OR c.id = svr.id)
      AND c.dtime IS NULL
LEFT JOIN {schema}.core_config_prop cc
       ON c.resource_conf_id = cc.configuration_id
WHERE svr.resource_type IN ('server.Server', 'platform.server')
  AND svr.dtime IS NULL
  AND svr.id > {cursor}
GROUP BY svr.id, svr.name, svr.hostname, svr.ipaddress,
         svr.uuid, svr.resource_key, svr.identifier,
         svr.location, svr.group_path, svr.manager_zone, svr.importance_id,
         svr.description, svr.avail_status
ORDER BY svr.id
FETCH FIRST {page_size} ROWS ONLY
```

**주의점**

- `dtime IS NULL`을 빼면 **삭제된 리소스가 섞인다.** 모든 쿼리에 붙인다.
- **`cmm_vendor`·`cmm_os`·`cmm_os_param`을 조인하지 않는다** (§2.3). `cmm_resource`에
  `vendor_id`·`os_id`·`os_param_id` 컬럼이 있어 FK처럼 보이지만 레거시이며, 값이 EAV와 다를 수 있다.
- **`cmm_metric_stat_*`·`cmm_alarm*`을 조인하지 않는다** (§1.1 비목표).
- `resource_type`은 사이트마다 `server.Server` / `platform.server%`로 갈린다 [검증 필요 — §15].
  `IN` 목록을 소스 설정으로 뺄 수 있게 만든다.
- `OSParameter`는 LOB이라 `stringvalue_short`가 빈 값일 수 있다. **이번 범위에서는 수집하지 않는다**
  (커널 파라미터 전문은 인벤토리 속성이 아니다). 필요해지면 `COALESCE(cc.stringvalue, cc.stringvalue_short)`를 쓴다.
- `TotalSize`는 `'62.1 GB'` 같은 **단위 포함 문자열**이다. 파서는 §9.2.

### 8.2 페이징 — 방언 분기

| 엔진 | 구문 |
|---|---|
| PostgreSQL | `FETCH FIRST n ROWS ONLY` (표준. `LIMIT`도 되지만 하나로 통일한다) |
| DB2 | `FETCH FIRST n ROWS ONLY` |

**두 엔진 모두 표준 구문을 지원하므로 `LIMIT`을 쓰지 않는다.** `OFFSET` 기반 페이징도 쓰지 않는다 —
동기화 중 데이터가 바뀌면 행이 누락되거나 중복된다. **`id > cursor` 키셋 페이징**을 쓴다.

DB2는 컬럼명을 대문자로 반환하지만 MCP 서버가 소문자로 정규화한다 (`_execute_db2_sync`).
그래도 매퍼는 `row.get("hostname") or row.get("HOSTNAME")` 형태로 방어한다.

### 8.3 NIC (MAC·IP) — 별도 쿼리

서버당 여러 행이므로 §8.1과 합치면 GROUP BY가 무너진다.

```sql
SELECT
  nic.platform_resource_id AS server_native_id,
  nic.id                   AS nic_native_id,
  nic.name                 AS nic_name,
  MAX(CASE WHEN cc.name = 'MAC'    THEN cc.stringvalue_short END) AS mac,
  MAX(CASE WHEN cc.name = 'IP'     THEN cc.stringvalue_short END) AS ip,
  MAX(CASE WHEN cc.name = 'STATUS' THEN cc.stringvalue_short END) AS status
FROM {schema}.cmm_resource nic
LEFT JOIN {schema}.core_config_prop cc
       ON nic.resource_conf_id = cc.configuration_id
WHERE nic.resource_type = 'server.NetworkInterface'
  AND nic.dtime IS NULL
  AND nic.id > {cursor}
GROUP BY nic.platform_resource_id, nic.id, nic.name
ORDER BY nic.id
FETCH FIRST {page_size} ROWS ONLY
```

## 9. 도메인 모델과 정규화

### 9.1 엔티티

```python
# src/domain/external.py

@dataclass(frozen=True, slots=True)
class ExternalNic:
    native_id: str
    name: str | None
    mac: str | None            # 정규화된 12 hex
    ip: str | None
    status: str | None


@dataclass
class ExternalServerFact:
    """폴스타가 게스트 관점에서 관측한 서버 사실 1건."""

    source_id: UUID
    native_id: str
    display_name: str | None = None      # cmm_resource.name  (공동존: 장비 식별명)
    hostname: str | None = None          # cmm_resource.hostname (OS 호스트명)
    primary_ip: str | None = None
    serial_number: str | None = None
    polestar_uuid: str | None = None     # cmm_resource.uuid 원본 (용도 확인 전 — §15-4)
    resource_key: str | None = None
    identifier: str | None = None
    normalized_uuid: str | None = None   # 매칭 규칙 1 키
    uuid_source: str | None = None       # normalized_uuid가 나온 컬럼명 (evidence용)
    nics: tuple[ExternalNic, ...] = ()
    os_type: str | None = None
    os_version: str | None = None
    patch_level: str | None = None
    vendor: str | None = None
    model: str | None = None
    cpu: CpuSpec | None = None
    memory_mb: int | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    location: str | None = None
    group_path: str | None = None
    manager_zone: str | None = None
    importance_id: int | None = None
    description: str | None = None
    avail_status: int | None = None
    raw_facts: dict[str, str] = field(default_factory=dict)
    observed_at: datetime | None = None

    @property
    def is_available(self) -> bool:
        """폴스타 기준 가용 상태. 0=정상, 그 외=비정상 (collectorinfra 프로필 규칙)."""
        return self.avail_status == 0

    @property
    def looks_virtual(self) -> bool:
        """가상화 추정 힌트. 판정 근거로 쓰지 않는다 (§2.2)."""
        blob = f"{self.vendor or ''} {self.model or ''}".lower()
        return "vmware" in blob or "virtual" in blob or "microsoft" in blob
```

`ServerLink`, `MatchRule(IntEnum)`, `MatchCandidate`, `ExternalSource`도 같은 모듈에 둔다.

### 9.2 값 파서 — 폴스타는 값을 문자열로 준다

```python
def parse_size_mb(value: str | None) -> int | None:
    """'62.1 GB' / '3.9 GB' / '4096 MB' → MB 정수. 파싱 실패는 None (0이 아니다)."""


def parse_int(value: str | None) -> int | None:
    """'8' / '8.0' → 8. 폴스타는 숫자도 문자열이며 소수점이 붙는 경우가 있다."""
```

> **파싱 실패를 0으로 채우지 않는다.** "메모리 0MB"는 사실이 아니라 수집 실패다.
> 포탈의 "값 없음 vs 수집 불가" 원칙을 여기에도 적용한다.

### 9.3 Protocol

```python
# src/domain/ports.py 에 추가

@runtime_checkable
class ServerFactReader(Protocol):
    """외부 시스템이 관측한 서버 사실 조회 인터페이스.

    구현체는 조회만 수행한다. 이 파일은 arch_check의 읽기 전용 검사 대상이다 (§3).
    """

    @property
    def source_id(self) -> UUID: ...

    async def start_session(self) -> None: ...
    async def close_session(self) -> None: ...
    async def __aenter__(self) -> "ServerFactReader": ...
    async def __aexit__(self, *exc_info: object) -> None: ...

    async def check_connection(self) -> ConnectionCheckResult:
        """MCP 서버 도달 + 소스 health_check. 예외 대신 결과로 반환한다."""

    def list_server_facts(self) -> AsyncIterator[ExternalServerFact]:
        """서버 사실을 페이지 단위로 흘려보낸다."""

    def get_outcomes(self) -> Sequence[CollectionOutcome]: ...


FactReaderFactory = Callable[["ExternalSource"], ServerFactReader]
```

`ConnectionCheckResult`·`CollectionOutcome`은 기존 타입을 재사용한다. `CheckStage`는
`REACHABLE`(MCP 서버 도달) / `AUTHENTICATED`(세션 초기화) / `AUTHORIZED`(소스 health_check) 3단계로 쓴다.

## 10. 동기화 흐름

```
external_sync_service.sync(source)
  1. reader = factory(source);  async with reader:
  2. async for fact in reader.list_server_facts():     # 키셋 페이징
        배치(기본 500)마다 external_servers upsert
  3. 이번 동기화에서 보이지 않은 서버 → lifecycle='missing'  (삭제하지 않는다 §7.2)
  4. match_service.match_servers(vm_keys, facts) 실행
        - 자동 확정 링크 → external_server_links upsert
        - 후보/충돌     → external_match_candidates upsert (기존 pending 유지)
  5. external_sources.last_success_at / last_error 갱신
  6. 감사 로그 기록 (§12)
```

**실패해도 기존 스냅샷과 링크를 지우지 않는다.** `last_error`만 남기고 신선도로 표시한다.

### 10.1 실행 시점

| 단계 | 방식 |
|---|---|
| 이 Step 착수 시 | 관리자가 화면에서 수동 실행 (`POST .../sync`, 202) |
| Step 3 스케줄러가 있으면 | 하이퍼바이저 수집 **완료 후** 후속 작업으로 실행 |

**하이퍼바이저 수집보다 먼저 돌리지 않는다.** VM이 없으면 매칭할 대상이 없다.

### 10.2 부하 제어

기존 수집 규칙을 그대로 따른다 — 호출당 타임아웃 60s, 재시도 최대 3회, 소스 동시 실행 1개.
폴스타는 운영 모니터링 시스템이므로 **조회 부하를 주지 않는 것이 등록 조건**이다.
페이지 크기와 페이지 간 간격(기본 0.2s)을 설정으로 노출한다.

## 11. API

### 11.1 관리 (admin 전용)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/external-sources` | 소스 목록 + 마지막 동기화 상태 |
| POST | `/api/v1/external-sources` | 등록 (`mcp_source_name`, `db_dialect`, `db_schema`) |
| PATCH | `/api/v1/external-sources/{id}` | 수정 |
| DELETE | `/api/v1/external-sources/{id}` | 삭제 (스냅샷·링크 CASCADE — 2단계 확인) |
| POST | `/api/v1/external-sources/{id}/check` | 연결 테스트 (단계별 결과) |
| POST | `/api/v1/external-sources/{id}/sync` | 동기화 시작 → **202 Accepted** |

### 11.2 조회 — VM 응답에 중첩

`GET /api/v1/virtual-machines/{id}` 응답에 블록을 추가한다. **응답 계약은 최종형으로 만든다** (D-011).

```json
{
  "resource_id": "…",
  "name": "app-web-01",
  "guest": { "availability": "tools_not_installed", "unavailable_reason": "게스트 도구 미설치" },
  "external": {
    "polestar": {
      "status": "linked",
      "source_name": "공동존 폴스타(김포)",
      "match_rule": 1,
      "match_rule_label": "UUID 계열 (시리얼)",
      "matched_by": "auto",
      "server_name": "cocm-hdkapp01",
      "hostname": "app-web-01",
      "os_type": "LINUX",
      "os_version": "3.10.0-957.el7.x86_64",
      "patch_level": "…",
      "cpu": { "logical_cores": 8, "physical_cores": 4, "sockets": 2, "model": "Intel Xeon Gold 6248R" },
      "memory_mb": 63590,
      "agent_version": "7.6.26_6",
      "avail_status": 0,
      "location": "…",
      "group_path": "…",
      "observed_at": "2026-08-14T02:10:00Z",
      "stale": false,
      "discrepancies": [
        { "field": "vcpu", "portal": 4, "polestar": 8, "note": "게스트가 인식한 논리 코어 수" }
      ]
    }
  }
}
```

**`status`는 4값으로 구분한다** — "값 없음"과 "수집 불가"를 섞지 않는 원칙의 연장이다.

| status | 의미 | 화면 표시 |
|---|---|---|
| `linked` | 매칭 확정 | 정보 표시 |
| `candidate` | 후보만 있음 | "확인 필요" + 후보 수 |
| `unmatched` | 동기화했으나 대응 서버 없음 | "폴스타 미등록" |
| `not_configured` | 폴스타 소스 미등록/미동기화 | "연동 안 됨" (미등록과 다르다) |

### 11.3 매칭 후보 (operator 이상)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/external-match-candidates?status=pending&offset=&limit=` | **`AccessScope` 필수** |
| POST | `/api/v1/external-match-candidates/{id}/accept` | 링크 확정 (`matched_by`=사용자명) |
| POST | `/api/v1/external-match-candidates/{id}/reject` | 후보 기각 |
| DELETE | `/api/v1/virtual-machines/{id}/external-link` | 링크 해제 |

> **범위 필터를 빠뜨리지 않는다.** 계획 09 §10이 지목한 이 프로젝트의 가장 흔한 결함이며,
> ROADMAP §26의 리스크 항목이다. 후보 목록은 VM을 노출하므로 **VM의 `connection_id` 기준으로 필터**한다.
> `external_servers` 단독 조회 API는 **만들지 않는다** — 범위 밖 자원 정보가 새는 경로가 된다.

## 12. 보안·감사

| 항목 | 규칙 |
|---|---|
| 자격증명 | **포탈은 폴스타 DB 자격증명을 보관하지 않는다** (MCP 서버가 관리). `external_sources`에 비밀 값이 없다 |
| 쓰기 금지 | SELECT만 전송. 포탈측 가드 + MCP 서버 `validate_readonly()` 이중 방어 |
| MCP 서버 접근 통제 | MCP SSE 엔드포인트에 인증이 없다면 **네트워크 ACL로만 보호된다.** 포탈 서버 IP만 허용되는지 확인 [검증 필요 — §15] |
| 감사 로그 | `external_source.create/update/delete`, `external_sync.run`, `external_link.confirm/reject/unlink` |
| 권한 | 소스 관리 = admin, 후보 확정 = operator 이상, 보강 정보 조회 = 해당 VM 조회 권한과 동일 |
| 로그 | SQL 전문을 INFO로 남기지 않는다. 소스명·행 수·소요 시간만 기록 |

## 13. UI

| 화면 | 대상 | 내용 |
|---|---|---|
| VM 상세 — "폴스타 정보" 섹션 | 전체 | §11.2의 값, 신선도 배지, 불일치 항목 강조, 미매칭 사유 |
| `external-sources.html` | admin | 소스 목록·등록·연결 테스트·수동 동기화·마지막 결과 |
| `match-candidates.html` | operator+ | 후보 목록(근거 표시), 확정/기각, 충돌 표시 |

**표시 규칙** (`docs/03_design_system.md` 토큰 사용)

- **조작 버튼을 넣지 않는다.** 폴스타에도 쓰기를 하지 않는다. "동기화"는 포탈 자신의 작업이며
  폴스타를 변경하지 않는다는 문구를 화면에 둔다.
- 폴스타 값과 하이퍼바이저 값을 **나란히 표시**하고 출처를 병기한다 (FR-304). 폴스타 값이
  하이퍼바이저 값을 대체하는 것처럼 보이면 안 된다.
- `stale`이면 "최종 관측 N시간 전"을 함께 표시한다.

## 14. 테스트

| 유형 | 내용 |
|---|---|
| 단위 — 정규화 | 시리얼 `VMware-42 1c …` → 32 hex, 물리 시리얼 → None, MAC 구분자 제거, FQDN 절단 |
| 단위 — 파서 | `'62.1 GB'` → 63590MB, `'8.0'` → 8, 빈 값 → None (**0이 아님**) |
| 단위 — 매칭 엔진 | 1:1 자동 확정 / 1:N 충돌 시 자동 확정 금지 / 규칙 3·4는 후보만 / 사용자 확정 보호(§6.4) |
| 단위 — MCP 응답 | `{"error": …}` → 예외, `truncated: true` → 예외, 정상 응답 파싱 |
| 단위 — **조회 테이블 고정** | `queries.py`가 만드는 모든 SQL에 `cmm_resource`·`core_config_prop` 외 테이블이 없음. `cmm_vendor`·`cmm_os`·`cmm_os_param`·`cmm_metric_stat_*`·`cmm_alarm*`이 등장하면 **실패** (§2.3, §1.1) |
| 계약 — `ServerFactReader` | fake 리더로 세션 열기·순회·닫기·outcome 계약 검증 |
| 통합 | fake MCP 클라이언트 픽스처로 동기화 → 스냅샷 → 링크 → VM 응답 `external.polestar` 확인 |
| 회귀 — 부분 실패 | **폴스타 소스 장애 상태에서 VM 목록·상세가 정상 동작**하고 기존 링크가 유지됨 |
| 회귀 — 범위 | 범위 밖 VM의 후보가 `AccessScope`로 걸러짐 |
| arch_check | `application`/`orchestration`이 `infrastructure.polestar`를 import하지 않음 |

**실제 폴스타 DB에 붙는 테스트는 작성하지 않는다** (CST-04). 픽스처는 collectorinfra의
`schema/polestar-data.md` 샘플 구조를 따른다.

## 15. 착수 전 확인이 필요한 항목

| # | 항목 | 확인 방법 | 미확인 시 영향 |
|---|---|---|---|
| 1 | 포탈 서버에서 **DBHub MCP(:9099) 도달 가능 여부**와 인증 유무 | 네트워크·`list_sources` 호출 | 경로 자체가 성립하지 않는다 |
| 2 | 연동 대상 폴스타 인스턴스 목록과 각 `source name`·DB 엔진·스키마명 | `list_sources` + 운영팀 확인 | 소스 등록값이 틀리면 "알 수 없는 소스" |
| 3 | **폴스타 DB 전체 테이블 목록** (사이트별 차이 포함) | `search_objects(source, "*")` — §2.5 | 이 계획서 §2.1이 collectorinfra 조회 범위 기준이라 누락이 있을 수 있다 |
| 4 | **`cmm_resource.uuid` 컬럼의 용도** — VM UUID인가, 폴스타 내부 식별자인가 | 가상 서버 표본의 값 형식 확인 + vCenter `config.uuid` 대조 | **매칭 규칙 1의 최선 경로다.** 확인 전에는 이 컬럼을 매칭에 쓰지 않는다 (§6.2 `resolve_uuid`) |
| 5 | **`SerialNumber` 실제 포맷** — VMware 게스트가 BIOS UUID와 대응되는가 | vCenter VM 1대의 `config.uuid`와 폴스타 시리얼 대조 | 4번이 실패하면 **이것이 유일한 규칙 1 경로**다 |
| 6 | Hyper-V/SCVMM 게스트의 시리얼·`BIOSGUID` 대응 | 동일 방식 (계획 05 §8.3·8.4의 값과 대조) | Hyper-V VM은 규칙 2·3으로만 매칭 — 결함이 아니다 (§6.1.1) |
| 7 | **`acc_*` 3개 테이블 구조와 `acl_manager_id` → 담당자명 조인 경로** | `get_table_schema` — §2.4 | 9-B 담당자 제안 가능 여부가 갈린다 |
| 8 | 각 사이트의 서버 `resource_type` 값 (`server.Server` / `platform.server%`) | 표본 쿼리 | 서버가 0건 조회된다 |
| 9 | `platform_resource_id`가 서버 자신에게도 설정되는가 | 표본 쿼리 | §8.1의 `OR c.id = svr.id` 필요 여부 |
| 10 | 공동존 외 사이트의 `name`/`hostname` 관계 | 표본 비교 | 매칭 후보 생성 방식 (§2.6) |
| 11 | 폴스타 서버 총 대수와 §8.1 쿼리 1페이지 응답 시간 | 실측 | 페이지 크기·타임아웃 기본값 조정 |
| 12 | MCP 서버 `max_rows`·`query_timeout` 실제 설정값 | `list_sources` 응답 | `truncated` 빈발 |
| 13 | `importance_id` 값의 의미(등급 체계) | 표본 + 운영팀 확인 | 중요도 제안값을 잘못 매핑한다 |

**1~3번은 착수 첫날에 확인한다** (§2.5의 탐색 절차). **4·5번은 9-4 착수 전까지 반드시 닫는다** —
매칭 규칙 1의 존립이 걸려 있다. 결과는 `docs/04_field_validation.md`에 기록하고,
이 계획서 §2.1·§6.1을 실제 확인값으로 갱신한다.

## 16. 범위 밖 — 이 단계에서 하지 않는다

| 항목 | 이유 |
|---|---|
| 모니터링 등록 갭 분석 화면 | 사용자가 이번 범위에서 제외. 데이터(`unmatched` 상태)는 이미 쌓이므로 나중에 조회만 얹으면 된다 |
| 물리 서버 인벤토리 확장 | `spec.md` §1.1 "가상자원 인벤토리" 범위를 넘는다. 하려면 spec 개정이 선행 |
| 폴스타 성능 지표·알람 수집 | `spec.md` §1.2 **명시적 비목표** |
| 폴스타 실시간 프로세스 API 연동 | 모니터링 영역 |
| 레거시 lookup(`cmm_vendor`·`cmm_os`·`cmm_os_param`) 조회 | 값이 EAV와 불일치할 수 있다 (§2.3, collectorinfra D-028) |
| 파일시스템·프로세스·HBA 등 하위 리소스 수집 | 이번 범위는 매칭 키(NIC)와 서버 사실까지다. 필요해지면 계층을 확장한다 |
| `acc_*` 담당자 원본의 포탈 축적 | 개인정보. 제안값 표시까지만 (§2.4) |
| 자연어 질의(collectorinfra의 LLM 파이프라인) | 별개 제품의 역할 |
| 폴스타 메타데이터 **자동 반영** | FR-602 위반. 제안값 제시까지만 (§17) |

## 17. 메타데이터 제안 — Step 7 의존

목적 ②(조직 메타데이터 초기값)는 **포탈 메타데이터 테이블이 있어야 성립한다.**
그 테이블은 ROADMAP §22 **Step 7**에서 만들어진다.

| 조각 | 선행 조건 |
|---|---|
| **9-A 보강** (§4~§14 전부) | Step 4 완료 (MAC·IP 수집으로 매칭 규칙 2·4 성립) |
| **9-B 메타데이터 제안** | **Step 7 완료** (`resource_metadata` 테이블) |

9-B의 동작은 다음으로 한정한다.

- 폴스타 `group_path`·`location`·`description`을 **제안값으로 표시**하고, 운영자가 버튼으로 채택
- **자동 반영·자동 덮어쓰기 금지** (FR-602). 재수집이 포탈 입력값을 덮어쓰지 않는다는 규칙은
  외부 소스에도 동일하게 적용된다
- 채택 이력은 감사 로그에 남긴다

> Step 4 이후에 9-A를 착수하되, **9-B는 Step 7 완료 전까지 착수하지 않는다.**
> 메타데이터 테이블 없이 제안 기능을 만들면 저장할 곳이 없다.

## 18. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 9-1 | D-019 기록 + **§2.5 스키마 탐색 수행** (§15의 1~3번) | 실제 테이블 목록·소스명·엔진 확정, 계획서 §2.1 갱신 |
| 9-1b | §15의 **4·5번(UUID 계열 매칭 키) 확인** | 규칙 1의 출처 확정. 여기서 막히면 9-7 설계가 바뀐다 |
| 9-2 | `src/domain/external.py` + `ports.ServerFactReader` + arch_check 등록 | `python scripts/arch_check.py` 통과 |
| 9-3 | 마이그레이션 `000X_polestar_enrichment` (§7) | `alembic upgrade head` → `downgrade` 왕복 |
| 9-4 | `mcp_client.py` + `queries.py` + `mapper.py` | 단위 테스트 (정규화·파서·MCP 응답) |
| 9-5 | `reader.py` (`PolestarFactReader`) | 계약 테스트 통과 |
| 9-6 | `external_repo.py` (upsert·missing 처리) | 2회 동기화 → 레코드 1건 |
| 9-7 | `server_match_service.py` (순수 함수) | 충돌·우선순위 단위 테스트 |
| 9-8 | `external_sync_service.py` + 팩토리 배선(`api/deps.py`) | 통합 테스트 |
| 9-9 | API 라우트 + 스키마 | 인증·범위·감사 테스트 |
| 9-10 | UI 3종 | 조작 버튼 없음 확인 (계획 11 §6.1) |
| 9-11 | 실환경 1개 소스로 매칭 정확도 실측 | §19 완료 기준 |
| 9-12 | (Step 7 이후) 9-B 메타데이터 제안 | FR-602 보존 테스트 |

**9-4~9-7은 서로 독립적이므로 worktree 병렬이 가능하다.** 공유 파일은 `ports.py`·`deps.py`뿐이다.

## 19. 완료 기준

1. 폴스타 소스 1개를 등록하고 동기화하면 `external_servers`가 채워지고, **VM 상세에 폴스타 정보가 표시**된다.
2. **MCP 서버를 중지한 상태에서 VM 목록·상세가 정상 동작**하고, 폴스타 섹션만 신선도 경고를 표시한다.
   기존 링크와 스냅샷이 **삭제되지 않는다.**
3. 자동 확정은 **규칙 1·2에서 1:1인 경우에만** 일어난다. 규칙 3·4는 후보로만 남는다.
4. 표본 **20건을 수동 검증**하여 오매칭 0건. 1건이라도 틀리면 해당 규칙을 후보 전용으로 강등한다.
5. 재동기화 2회 후 `external_servers` 중복 0건, 링크 중복 0건.
6. 폴스타에 전송된 SQL이 **전부 SELECT**임이 로그로 확인된다.
7. `python scripts/arch_check.py --ci` 통과 — `application`/`orchestration`에 폴스타 import 없음.
8. 소스 등록·동기화·매칭 확정이 **감사 로그에 기록**된다.
9. 범위 밖 VM의 매칭 후보가 조회되지 않는다 (`AccessScope` 적용).

## 20. 주의사항

| 위험 | 징후 | 대응 |
|---|---|---|
| **오매칭이 조용히 쌓인다** | 호스트명/IP 규칙을 자동 확정으로 바꾸자는 요구 | §6.1 등급을 바꾸지 않는다. 정확도는 §19-4로 측정한다 |
| **`truncated`를 놓쳐 인벤토리에 구멍** | 특정 사이트만 서버 수가 적다 | §5.2-2. `truncated`는 실패로 처리 |
| **성능 지표로 범위가 번진다** | "이왕 붙은 김에 CPU 사용률도" | `spec.md` §1.2 비목표. 요청 시 범위 확인부터 |
| **조회 테이블이 슬금슬금 는다** | 알람·성능·lookup 테이블 조인이 SQL에 추가됨 | §14의 조회 테이블 고정 테스트가 CI에서 막는다. 늘리려면 D-019를 먼저 고친다 |
| 폴스타에 부하를 준다 | 폴스타 운영팀의 조회 부하 문의 | §10.2 페이지 크기·간격 조정, 야간 동기화 |
| 조회 경로가 폴스타에 의존하게 된다 | "최신 값을 보려면 실시간 조회" 요구 | D-007. 조회는 저장소 경유. 실시간이 필요하면 동기화 주기를 줄인다 |
| MCP 서버가 다른 팀 소유라 예고 없이 바뀐다 | 소스명 변경, 스키마 변경 | `check` API를 스케줄로 돌려 조기 감지. 실패는 `last_error`로 노출 |
| 공동존 `name`/`hostname` 혼동 | 특정 사이트만 매칭률이 낮다 | §2.3. 둘 다 후보로 쓰고 근거에 어느 쪽이 맞았는지 기록 |
