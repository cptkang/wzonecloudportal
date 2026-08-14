# 08. FastAPI 서버 및 연결 관리 API

> Wave: 4 · 계층: interface (`src/api/`) · application (`connection_service.py`)
> 담당 요건: FR-1xx, FR-11xx, FR-4xx·5xx·6xx·7xx 노출
> 의존: 03, 06, 07, 09, 10, 12 · 관련 결정: D-005, D-007, D-008

## 1. 목적

포탈 UI와 외부 시스템에 REST API를 제공하고, 하이퍼바이저 연결을 관리한다.

**하이퍼바이저 자원을 변경하는 엔드포인트는 존재하지 않는다** (D-005).
포탈 자체 데이터(연결 설정, 메타데이터, 사용자)만 변경 가능하다.

## 2. 구성

```
src/api/
├── __init__.py
├── app.py                 FastAPI 앱 팩토리
├── deps.py                DI, 인증·범위 의존성 (계획 09 §6)
├── errors.py              예외 핸들러
├── schemas/
│   ├── common.py          PagedResponse, ErrorResponse
│   ├── connection.py      연결 요청·응답
│   ├── inventory.py       자원 응답
│   ├── metadata.py
│   └── report.py
└── routes/
    ├── health.py  auth.py  connections.py  inventory.py
    ├── metadata.py  history.py  quality.py  reports.py  dashboard.py
```

---

## 3. 공통 규약

### 3.1 응답 형식

```python
class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int
    total_is_estimate: bool = False


class ErrorResponse(BaseModel):
    error: str                  # 기계 판독용 코드
    message: str                # 사용자 표시용 (한국어)
    detail: dict[str, Any] = {}
```

### 3.2 예외 → HTTP 매핑 (`errors.py`)

```python
EXCEPTION_STATUS: list[tuple[type[Exception], int, str]] = [
    (ValidationError,      422, "validation_error"),
    (AuthenticationError,  401, "authentication_failed"),
    (PermissionError,      403, "permission_denied"),
    (NotFoundError,        404, "not_found"),
    (DuplicateError,       409, "duplicate"),
    (CollectionError,      502, "collection_error"),
]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PortalError)
    async def _handle(request: Request, exc: PortalError) -> JSONResponse:
        for exc_type, status, code in EXCEPTION_STATUS:
            if isinstance(exc, exc_type):
                return JSONResponse(
                    status_code=status,
                    content=ErrorResponse(
                        error=code, message=exc.message,
                        detail={**exc.detail, **({"field": exc.field} if getattr(exc, "field", None) else {})},
                    ).model_dump(),
                )
        logger.exception("처리되지 않은 도메인 오류")
        return JSONResponse(500, ErrorResponse(error="internal_error",
                                               message="처리 중 오류가 발생했습니다.").model_dump())
```

**500 응답에 내부 상세를 넣지 않는다.** 스택·SQL·경로가 노출되면 공격 표면이 된다.

> **주의**: `AuthenticationError`는 두 의미로 쓰인다 — 포탈 로그인 실패(401)와 하이퍼바이저 인증 실패.
> 후자는 **연결 테스트 결과로 반환**되며 HTTP 에러가 아니다 (§5.4).

### 3.3 인증·권한

- `Depends(get_access_scope)`로 범위 주입 (계획 09 §6)
- 라우터에 `require(Permission.X)` + **유스케이스에서 재검사** (이중 방어)
- 조회 범위는 유스케이스가 SQL에 반영. API가 결과를 후처리하지 않는다

---

## 4. 엔드포인트 목록

| 메서드 | 경로 | 요건 | 권한 |
|---|---|---|---|
| GET | `/api/v1/health` | — | 없음 |
| POST | `/api/v1/auth/register` | **FR-1006** | 없음 (호출량 제한) |
| POST | `/api/v1/auth/login` | FR-1001 | 없음 |
| POST | `/api/v1/auth/logout` | — | 인증 |
| GET | `/api/v1/auth/me` | — | 인증 |
| POST | `/api/v1/auth/change-password` | **FR-1008** | 인증 |
| GET | `/api/v1/users` | **FR-1007** | admin |
| GET | `/api/v1/users/{id}` | FR-1007 | admin |
| PATCH | `/api/v1/users/{id}` | FR-1007 (역할·표시이름) | admin |
| POST | `/api/v1/users/{id}/approve` | **FR-1006·1007** | admin |
| POST | `/api/v1/users/{id}/reject` | FR-1006 | admin |
| POST | `/api/v1/users/{id}/disable` `/enable` | FR-1007 | admin |
| PUT | `/api/v1/users/{id}/scopes` | FR-1003 | admin |
| POST | `/api/v1/users/{id}/reset-password` | FR-1008 | admin |
| GET | `/api/v1/connections` | FR-116 | admin |
| POST | `/api/v1/connections` | FR-101·103·104·105 | admin |
| GET | `/api/v1/connections/{id}` | FR-113 | admin |
| PATCH | `/api/v1/connections/{id}` | FR-107·108·110·111 | admin |
| DELETE | `/api/v1/connections/{id}` | FR-109 | admin |
| POST | `/api/v1/connections/test` | FR-106 (저장 전) | admin |
| POST | `/api/v1/connections/{id}/test` | FR-106 | admin |
| POST | `/api/v1/connections/{id}/collect` | FR-202 | admin |
| GET | `/api/v1/connections/{id}/runs` | FR-205 | admin |
| GET | `/api/v1/virtual-machines` | FR-401·405·406 | viewer |
| GET | `/api/v1/virtual-machines/{id}` | FR-402 | viewer |
| GET | `/api/v1/virtual-machines/{id}/related` | FR-409 | viewer |
| GET | `/api/v1/virtual-machines/{id}/history` | FR-705 | viewer |
| GET | `/api/v1/hosts` `/clusters` `/datastores` `/networks` | FR-401 | viewer |
| GET | `/api/v1/hosts/{id}/virtual-machines` | FR-409 | viewer |
| GET | `/api/v1/search` | FR-403 | viewer |
| GET | `/api/v1/search/by-ip` | **FR-404** | viewer |
| PATCH | `/api/v1/resources/{id}/metadata` | FR-601 | operator |
| POST | `/api/v1/resources/metadata/bulk` | FR-604 | operator |
| POST | `/api/v1/resources/metadata/import/preview` | FR-605 | operator |
| POST | `/api/v1/resources/metadata/import/apply` | FR-605 | operator |
| GET | `/api/v1/quality/summary` `/tools-missing` `/stale` `/metadata-missing` | FR-503·504·505 | viewer |
| GET | `/api/v1/changes` | FR-703·704 | viewer |
| GET | `/api/v1/duplicates` | FR-308 | admin |
| POST | `/api/v1/duplicates/{id}/dismiss` | FR-308 | admin |
| GET | `/api/v1/dashboard/summary` | FR-9xx | viewer |
| GET | `/api/v1/reports/*` | FR-8xx | viewer |

### 4.1 인증·계정 API (FR-1001·1006·1007·1008)

상세 규칙은 계획 09 §4.5·4.6에 있다. 여기서는 HTTP 계약만 고정한다.

```python
# 가입 — 중복 여부를 노출하지 않기 위해 항상 202를 반환한다 (계획 09 §4.5)
@router.post("/auth/register", status_code=202)
async def register(req: RegisterRequest, request: Request, svc=Depends(get_registration_service)):
    await svc.register(req, client_ip(request))
    return {"message": "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."}


# 로그인 — 토큰을 본문이 아니라 httpOnly 쿠키로 내려보낸다 (D-014)
@router.post("/auth/login")
async def login(req: LoginRequest, response: Response, request: Request, svc=Depends(get_auth_service)):
    token = await svc.login(req.username, req.password, client_ip(request))
    set_session_cookie(response, token, settings)
    return {"user": UserResponse.from_domain(await svc.current_user(token))}
```

| 엔드포인트 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `POST /auth/register` | `username`·`password`·`display_name`·`email` | **202** + 안내 문구 | 중복이어도 동일 응답 |
| `POST /auth/login` | `username`·`password` | 200 + `user` + **Set-Cookie** | 토큰을 본문에 넣지 않는다 |
| `POST /auth/logout` | — | 204 + 쿠키 삭제 | |
| `GET /auth/me` | — | `user` + `permissions[]` + `must_change_password` | **UI 메뉴 노출 판단에 쓴다** (FR-1213) |
| `POST /auth/change-password` | `current_password`·`new_password` | 204 | 현재 비밀번호 확인 필수 |

**사용자 관리** — 모두 `admin` 전용이며 `Permission.USER_MANAGE`를 검사한다.

| 엔드포인트 | 본문 | 비고 |
|---|---|---|
| `GET /users?status=&page=&size=` | — | `status=pending`이 관리자 화면 기본 필터 |
| `POST /users/{id}/approve` | `role`·`connection_ids[]` | **역할과 범위를 승인과 한 번에 부여** |
| `POST /users/{id}/reject` | `reason?` | |
| `POST /users/{id}/disable` · `/enable` | — | |
| `PATCH /users/{id}` | `role?`·`display_name?` | |
| `PUT /users/{id}/scopes` | `connection_ids[]` | 전체 교체(멱등) |
| `POST /users/{id}/reset-password` | — | 응답에 **임시 비밀번호 1회만** 포함 |

> **`DELETE /users/{id}`를 만들지 않는다.** 계정은 비활성화만 한다 (D-014).
> 감사 로그의 행위자 참조가 끊기면 "누가 이 연결을 등록했는가"를 추적할 수 없다.

**응답에 `password_hash`·`locked_until`을 넣지 않는다.** `UserResponse`에 필드를 정의하지 않는 것으로 강제한다.

### 4.2 인증이 필요 없는 경로

```python
PUBLIC_PATHS = {"/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/register"}
```

**이 집합은 화이트리스트다.** 새 엔드포인트는 기본적으로 인증이 필요하며,
공개가 필요하면 여기에 명시적으로 추가한다. 반대로 만들면 인증 누락이 조용히 생긴다.

`/auth/register`는 인증이 없으므로 **호출량 제한을 반드시 적용한다** (계획 09 §4.5).

---

## 5. 연결 관리 API (FR-1xx) — 이 계획의 핵심

### 5.1 요청 스키마 — 연결 유형별 분기 (FR-103)

**Pydantic discriminated union으로 유형별 필수 필드를 강제한다.**
단일 스키마로 만들면 Hyper-V의 인증 방식 필수 여부를 표현할 수 없다.

```python
class _ConnectionBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    address: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1)
    verify_tls: bool = True
    collection_interval_minutes: int = Field(default=360, ge=5, le=10080)

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        v = v.strip()
        if not (_is_valid_hostname(v) or _is_valid_ip(v)):
            raise ValueError("올바른 FQDN 또는 IP 주소가 아닙니다.")
        return v


class VCenterConnectionCreate(_ConnectionBase):
    kind: Literal[ConnectionKind.VCENTER]
    port: int = Field(default=443, ge=1, le=65535)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        if "@" not in v and "\\" not in v:
            raise ValueError("vCenter 계정은 user@domain 형식을 권장합니다.")
        return v


class HyperVConnectionCreate(_ConnectionBase):
    """경로 A — Hyper-V 관리자 계열. 호스트 또는 클러스터 (계획 05 §2)."""
    kind: Literal[ConnectionKind.HYPERV_HOST, ConnectionKind.HYPERV_CLUSTER]
    port: int = Field(default=5986, ge=1, le=65535)
    protocol: Literal["http", "https"] = "https"
    auth_method: WinRmAuth                       # 필수 — Hyper-V만 (FR-103)
    session_configuration: str | None = None     # JEA 엔드포인트 이름 (계획 05 §4.3.1)


class ScvmmConnectionCreate(_ConnectionBase):
    """경로 B — SCVMM 관리 서버 1대가 fabric 전체를 대표한다 (D-012).

    address는 **SCVMM 서버 자신**이어야 한다. 콘솔만 설치된 서버를 경유하면
    이중 홉이 되어 CredSSP가 필요해진다 (계획 05 §4.2).
    """
    kind: Literal[ConnectionKind.SCVMM]
    port: int = Field(default=5986, ge=1, le=65535)
    protocol: Literal["http", "https"] = "https"
    auth_method: WinRmAuth                       # WinRM 접속이므로 경로 A와 동일하게 필수


ConnectionCreate = Annotated[
    VCenterConnectionCreate | HyperVConnectionCreate | ScvmmConnectionCreate,
    Field(discriminator="kind"),
]
```

**SCVMM과 Hyper-V 호스트를 동시에 등록하면 같은 VM이 두 자원으로 생성된다** (계획 05 §2.1).
등록 자체를 막지는 않는다 — SCVMM이 관리하지 않는 독립 호스트가 있을 수 있다.
대신 **SCVMM 연결이 이미 있는 상태에서 `hyperv-host`를 등록하면 응답에 경고를 포함**하고,
UI가 확인 다이얼로그를 띄운다. 차단이 아니라 경고인 이유는 어느 호스트가 SCVMM 관리 대상인지
포탈이 등록 시점에는 알 수 없기 때문이다.

### 5.2 응답 스키마 — 비밀번호 부재

```python
class ConnectionResponse(BaseModel):
    connection_id: UUID
    kind: ConnectionKind
    display_name: str
    description: str | None
    address: str
    port: int
    protocol: str
    auth_method: WinRmAuth | None
    username: str
    has_password: bool = True                    # 값이 아니라 존재 여부만 (NFR-203)
    verify_tls: bool
    collection_interval_minutes: int
    status: ConnectionStatus
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    resource_counts: dict[str, int] | None = None
```

**`password` 필드가 스키마에 아예 없다.** 있으면 언젠가 채워진다.

### 5.3 등록 (FR-101·104·105)

```python
@router.post("", status_code=201, dependencies=[Depends(require(Permission.CONNECTION_MANAGE))])
async def create_connection(
    payload: ConnectionCreate,
    scope: AccessScope = Depends(get_access_scope),
    svc: ConnectionService = Depends(get_connection_service),
    request: Request = None,
) -> ConnectionResponse:
    return await svc.create(scope, payload, actor=scope.username, ip=_client_ip(request))
```

```python
class ConnectionService:
    async def create(self, scope, payload, actor: str, ip: str | None) -> ConnectionResponse:
        scope.require(Permission.CONNECTION_MANAGE)

        # FR-105 중복 등록 방지 — DB UNIQUE 제약과 함께 이중 방어
        if await self._repo.exists(payload.address, payload.username):
            existing = await self._repo.find_by_target(payload.address, payload.username)
            raise DuplicateError(
                "동일한 주소와 계정의 연결이 이미 등록되어 있습니다.",
                detail={"existing_connection_id": str(existing.connection_id),
                        "existing_display_name": existing.display_name},
            )

        conn = _to_domain(payload, connection_id=uuid4())
        conn.validate()                                        # 도메인 규칙 (계획 02 §10)
        encrypted = self._cipher.encrypt(payload.password)
        created = await self._repo.insert(conn, encrypted, actor)

        await self._scheduler.schedule(created)                # 주기 등록
        await self._audit.record(AuditEvent(
            actor=actor, actor_ip=ip, action=AuditAction.CONNECTION_CREATE,
            target_type="connection", target_id=str(created.connection_id), result="success",
            detail=build_detail(AuditAction.CONNECTION_CREATE, payload.model_dump(exclude={"password"})),
        ))
        return _to_response(created)
```

**`exclude={"password"}`를 빠뜨리면 감사 로그에 비밀번호가 들어간다.**
`build_detail`의 화이트리스트(계획 10 §6.2)가 2차 방어지만, 여기서도 명시적으로 제외한다.

### 5.4 연결 테스트 (FR-106)

```python
@router.post("/{connection_id}/test", dependencies=[Depends(require(Permission.CONNECTION_MANAGE))])
async def test_connection(connection_id: UUID, ...) -> ConnectionTestResponse: ...

@router.post("/test", dependencies=[Depends(require(Permission.CONNECTION_MANAGE))])
async def test_unsaved_connection(payload: ConnectionCreate, ...) -> ConnectionTestResponse:
    """저장 전 테스트. 자격증명을 저장하지 않고 임시 객체로만 사용한다."""
```

```python
class StageResponse(BaseModel):
    stage: CheckStage
    passed: bool
    skipped: bool
    detail: str | None
    elapsed_ms: int | None


class ConnectionTestResponse(BaseModel):
    is_usable: bool
    stages: list[StageResponse]
    readable_types: list[ResourceType]
    server_version: str | None
    failed_stage: CheckStage | None
```

**HTTP 200으로 반환한다.** 테스트 자체는 성공했고 결과가 실패인 것이다.
연결 실패를 500이나 502로 반환하면 클라이언트가 "요청 처리 실패"와 "연결 실패"를 구분할 수 없다.

**`detail`에 자격증명이 포함되지 않도록** 어댑터가 정제한 메시지만 전달한다 (계획 10 §5.2).

### 5.5 수정 (FR-107·108·110·111)

```python
class ConnectionUpdate(BaseModel):
    """미지정 필드는 변경하지 않는다. password를 생략하면 기존 값 유지 (FR-108)."""
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    description: str | None = None
    address: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: Literal["http", "https"] | None = None
    auth_method: WinRmAuth | None = None
    username: str | None = None
    password: SecretStr | None = None            # None = 변경 안 함
    verify_tls: bool | None = None
    collection_interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    is_active: bool | None = None
```

```python
async def update(self, scope, connection_id, payload: ConnectionUpdate, actor, ip) -> UpdateResult:
    scope.require(Permission.CONNECTION_MANAGE)
    current = await self._repo.get(connection_id)

    # model_fields_set으로 "미지정"과 "명시적 None"을 구분한다
    changes = payload.model_dump(exclude_unset=True, exclude={"password"})
    warnings: list[str] = []

    if "address" in changes and changes["address"] != current.address:
        warnings.append(
            "주소가 변경되었습니다. 다른 하이퍼바이저를 가리키는 경우 다음 수집에서 "
            "기존 자원이 대량 미발견 처리됩니다. 새 하이퍼바이저 추가라면 새 연결을 등록하세요."
        )                                                     # FR-110·111

    encrypted = None
    if "password" in payload.model_fields_set and payload.password is not None:
        encrypted = self._cipher.encrypt(payload.password)

    updated = await self._repo.update(connection_id, changes, encrypted, actor)

    # 저장 후 연결 테스트 재실행 (FR-107) — 실패해도 저장은 유지
    test = await self._tester.test(updated)
    if not test.is_usable:
        warnings.append(f"연결 테스트에 실패했습니다: {test.failed_stage}")

    # 자격증명 오류 상태였다가 갱신되면 재개 (계획 06 §8.3)
    if encrypted and test.is_usable and current.status is ConnectionStatus.CREDENTIAL_ERROR:
        await self._repo.update_status(connection_id, ConnectionStatus.ACTIVE)
        await self._scheduler.schedule(updated)
        warnings.append("자격증명이 갱신되어 수집이 재개됩니다.")

    await self._audit_update(actor, ip, connection_id, changes, credential_changed=bool(encrypted))
    return UpdateResult(connection=_to_response(updated), warnings=warnings, test=test)
```

**`connection_id`는 경로 파라미터로만 받고 본문에서 무시한다** (FR-110, `extra="forbid"`가 강제).

### 5.6 삭제 (FR-109) — 2단계 확인

```python
@router.delete("/{connection_id}", dependencies=[Depends(require(Permission.CONNECTION_MANAGE))])
async def delete_connection(
    connection_id: UUID,
    confirm: bool = Query(default=False),
    confirm_name: str | None = Query(default=None),
    ...
) -> DeleteResponse:
    conn = await svc.get(connection_id)
    impact = await svc.count_impact(connection_id)

    if not confirm:
        raise HTTPException(409, detail=ErrorResponse(
            error="confirmation_required",
            message="삭제하려면 확인이 필요합니다.",
            detail={
                "impact": {k.value: v for k, v in impact.items()},
                "policy": "자원은 삭제되지 않고 '연결 해제됨' 상태로 보존됩니다.",
                "required_confirm_name": conn.display_name,
            },
        ).model_dump())

    if confirm_name != conn.display_name:
        raise ValidationError("확인을 위해 연결 이름을 정확히 입력하세요.", field="confirm_name")

    return await svc.delete(scope, connection_id, actor, ip)
```

수천 건의 자원이 딸려 있으므로 **이름 입력 확인**까지 요구한다 (계획 11 §6.6).

### 5.7 수동 수집 (FR-202)

```python
@router.post("/{connection_id}/collect", status_code=202,
             dependencies=[Depends(require(Permission.COLLECTION_TRIGGER))])
async def trigger_collection(connection_id: UUID, ...) -> CollectionRunResponse:
    conn = await svc.get(connection_id)

    # 자격증명 오류 상태에서 반복 시도하면 AD 계정이 잠긴다 (CST-05)
    if conn.status is ConnectionStatus.CREDENTIAL_ERROR:
        raise ValidationError(
            "자격증명 오류 상태입니다. 자격증명을 갱신한 뒤 다시 시도하세요.",
            field="status",
        )
    if conn.status is ConnectionStatus.INACTIVE:
        raise ValidationError("비활성 연결입니다. 활성화 후 시도하세요.", field="status")

    try:
        run_id = await scheduler.trigger_now(connection_id, actor)
    except DuplicateError as exc:
        raise HTTPException(409, detail=ErrorResponse(
            error="already_running", message=exc.message,
            detail={"running_run_id": str(await svc.current_run_id(connection_id))},
        ).model_dump())
    return CollectionRunResponse(run_id=run_id, status="queued")
```

---

## 6. 자원 조회 API

### 6.1 목록 (FR-401·405·406)

```python
@router.get("/virtual-machines")
async def list_vms(
    scope: AccessScope = Depends(get_access_scope),
    # 필터 (FR-405)
    connection_id: list[UUID] = Query(default=[]),
    hypervisor: list[HypervisorKind] = Query(default=[]),
    cluster: list[str] = Query(default=[]),
    host: list[str] = Query(default=[]),
    power_state: list[PowerState] = Query(default=[]),
    os_contains: str | None = Query(default=None, min_length=2),
    environment: list[Environment] = Query(default=[]),
    owner: list[str] = Query(default=[]),
    guest_availability: list[GuestInfoAvailability] = Query(default=[]),
    lifecycle: list[ResourceLifecycle] = Query(default=[ResourceLifecycle.ACTIVE]),
    stale_hours: int | None = Query(default=None, ge=1),
    # 페이징
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    sort_by: str = Query(default="name"),
    sort_desc: bool = Query(default=False),
    svc: InventoryQueryService = Depends(get_inventory_service),
) -> PagedResponse[VmSummaryResponse]:
    ...
```

**`lifecycle` 기본값이 `[ACTIVE]`인 것이 중요하다** (계획 07 §3.3).

### 6.2 IP 역조회 (FR-404)

```python
@router.get("/search/by-ip")
async def search_by_ip(
    ip: str = Query(min_length=3, description="IPv4 또는 IPv6 주소"),
    include_inactive: bool = Query(default=False),
    scope: AccessScope = Depends(get_access_scope),
    svc: InventoryQueryService = Depends(get_inventory_service),
) -> list[VmSummaryResponse]:
    """IP로 VM을 찾는다. 결과가 복수일 수 있다 (분리망, 과거 IP 보유 자원)."""
    return [_to_response(vm) for vm in await svc.find_by_ip(scope, ip, include_inactive)]
```

### 6.3 응답의 "수집 불가" 표현 (FR-501)

```python
class GuestInfoResponse(BaseModel):
    availability: GuestInfoAvailability
    is_collected: bool
    unavailable_reason: str | None            # UNAVAILABLE_REASONS 매핑 (계획 02 §5.1)
    os_name: str | None
    os_source: OsSource | None
    hostname: str | None
    ipv4_addresses: list[str]
    ipv6_addresses: list[str]
    observed_at: datetime | None              # 마지막 확인 시각
```

**클라이언트가 빈 배열과 수집 불가를 구분할 수 있어야 한다.**
`is_collected=False`이고 `observed_at`이 과거면 "수집 불가 (마지막 확인: N일 전)"로 표시한다.

---

## 7. 외부 연동 API (FR-11xx)

### 7.1 API 키 인증 (FR-1104)

```python
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
```

- 발급 시 원문을 **1회만** 반환하고 이후 해시만 보관 (계획 09 §5)
- 키에도 조회 범위를 부여한다 (소유 사용자의 범위를 상속)
- `last_used_at`을 갱신하여 미사용 키를 식별

### 7.2 호출량 제한

```python
class RateLimiter:
    """Redis 슬라이딩 윈도우. 기본 60 req/min."""

    async def check(self, key: str, limit: int, window_sec: int = 60) -> RateLimitStatus:
        now = time.time()
        redis_key = f"ratelimit:{key}"
        async with self._redis.pipeline() as pipe:
            pipe.zremrangebyscore(redis_key, 0, now - window_sec)
            pipe.zadd(redis_key, {str(uuid4()): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window_sec)
            _, _, count, _ = await pipe.execute()
        return RateLimitStatus(allowed=count <= limit, remaining=max(0, limit - count))
```

초과 시 429와 `Retry-After` 헤더를 반환한다.

### 7.3 변경 이력 API (FR-1103) — 커서 기반

외부 CMDB 동기화용이므로 **offset 페이징을 쓰지 않는다.** 데이터가 계속 추가되면 누락이 발생한다.

```python
@router.get("/changes")
async def list_changes(
    since: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    ...
) -> CursorPagedResponse[ChangeResponse]:
    """커서는 (detected_at, change_id) 복합 키를 base64 인코딩한 값이다."""
```

```sql
WHERE (detected_at, change_id) > (:cursor_time, :cursor_id)
ORDER BY detected_at, change_id
LIMIT :limit
```

---

## 8. 앱 구성 (`src/main.py`)

```python
def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="클라우드 포탈 API", version="1.0.0", docs_url="/docs")
    install_error_handlers(app)
    install_masking(logging.getLogger().handlers)           # 계획 10 §5.1
    app.include_router(health.router, prefix="/api/v1")
    # ... 라우터 등록
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    return app


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["api", "worker"], default="api")
    args = parser.parse_args()

    settings = Settings()
    if args.mode == "api":
        await _run_api(settings)
    else:
        await _run_worker(settings)                          # 계획 06 Part B
```

**어댑터 팩토리를 여기서 구성해 주입한다** (계획 03 §7).
`api`/`entry` 계층만 `src.infrastructure.vcenter|hyperv`를 import할 수 있다.

### 8.1 헬스체크

```python
@router.get("/health")
async def health(db=Depends(get_db), redis=Depends(get_redis)) -> HealthResponse:
    checks = {
        "database": await _check_db(db),
        "redis": await _check_redis(redis),
    }
    status = "healthy" if all(c.ok for c in checks.values()) else "degraded"
    return HealthResponse(status=status, checks=checks, version=__version__)
```

---

## 9. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | 앱 팩토리 + `/health` | 200 응답, DB·Redis 상태 포함 |
| 2 | `errors.py` 예외 핸들러 | 예외별 상태 코드, 500에 내부 정보 부재 |
| 3 | 인증 라우터 + `deps.py` | 로그인, 401/403, 범위 주입 |
| 4 | 연결 CRUD | **유형별 스키마 분기**, 입력 검증 422, 중복 409 |
| 5 | 연결 테스트 | 200 + 단계별 결과, 자격증명 미노출 |
| 6 | 삭제 2단계 확인 | `confirm` 없이 409 + 영향 범위, 이름 불일치 시 422 |
| 7 | 수동 수집 | 자격증명 오류 시 거부, 중복 실행 409 |
| 8 | 자원 조회·검색 | 범위 필터, 페이징, IP 역조회 |
| 9 | 메타데이터 | 부분 갱신, 권한, 일괄·가져오기 미리보기 |
| 10 | 이력·품질·대시보드 | 범위 반영 |
| 11 | API 키 + 호출량 제한 | 429, `Retry-After` |
| 12 | 커서 페이징 변경 API | 데이터 추가 중에도 누락 없음 |
| 13 | OpenAPI 문서 | `/docs` 확인, 예시 포함 |

## 10. 완료 기준

- [ ] **하이퍼바이저 자원을 변경하는 엔드포인트가 없음** (전원·삭제·생성·마이그레이션 경로 부재)
- [ ] 연결 관리 API가 admin에게만 허용됨
- [ ] Hyper-V 등록 시 `auth_method` 필수, vCenter는 불가 (discriminated union)
- [ ] 잘못된 주소·포트·계정 형식이 422와 필드명을 반환
- [ ] 중복 주소+계정 등록이 409와 기존 연결 정보를 반환
- [ ] **비밀번호가 어떤 응답에도 포함되지 않음** (스키마에 필드 부재)
- [ ] 수정 시 `password` 생략하면 기존 값 유지
- [ ] 주소 변경 시 경고가 응답에 포함됨
- [ ] 삭제가 `confirm` + 이름 입력 없이는 수행되지 않고 영향 범위를 반환
- [ ] 자격증명 오류 연결의 수동 수집이 거부됨
- [ ] 연결 테스트 실패가 HTTP 200 + 단계별 결과로 반환됨
- [ ] 모든 조회에 범위 필터 적용 (범위 밖 자원 미노출)
- [ ] 감사 로그에 비밀번호가 기록되지 않음
- [ ] 변경 이력 API가 커서 기반으로 누락 없이 페이징
- [ ] `arch_check.py` 통과, `/security-review` 지적 0건

## 11. 주의사항

- **연결 테스트 응답의 `detail`에 원본 예외 메시지를 그대로 넣지 않는다.** pyVmomi·WinRM 예외에 접속 정보가 섞인다.
- 자격증명 오류 상태에서 수동 수집을 허용하면 관리자가 반복 클릭해 계정을 잠글 수 있다 (CST-05).
- 대시보드 집계에도 범위를 적용한다. 전체 건수 노출은 정보 누설이다 (계획 09 §10).
- 외부 API의 `since` 조회는 커서 기반으로 한다. offset은 이력이 계속 추가되는 상황에서 누락을 만든다.
- 감사 로그에 `payload.model_dump()`를 그대로 넣지 않는다. `exclude={"password"}`와 화이트리스트를 함께 쓴다.
- 500 응답에 스택·SQL을 노출하지 않는다.
