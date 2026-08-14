# 09. 인증·권한·조회 범위 제한

> Wave: 2 · 계층: domain (`auth.py`) · infrastructure (`security/`)
> 담당 요건: FR-1001~1003, FR-1005, NFR-204·210
> 의존: 01, 02, 10 · 관련 결정: D-005

## 1. 목적

포탈 사용자를 인증하고, 역할과 조회 범위로 접근을 제한한다.

**인벤토리 정보(IP·호스트명·OS)는 그 자체로 공격 표면 정보**이므로(NFR-206),
조회 범위 제한이 이 프로젝트의 핵심 보안 통제다.

---

## 2. 도메인 모델 (`src/domain/auth.py`)

```python
class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(StrEnum):
    RESOURCE_READ = "resource.read"
    METADATA_WRITE = "metadata.write"
    CONNECTION_MANAGE = "connection.manage"
    COLLECTION_TRIGGER = "collection.trigger"
    USER_MANAGE = "user.manage"
    EXPORT = "export"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({
        Permission.RESOURCE_READ,
        Permission.EXPORT,
    }),
    Role.OPERATOR: frozenset({
        Permission.RESOURCE_READ,
        Permission.METADATA_WRITE,
        Permission.EXPORT,
    }),
    Role.ADMIN: frozenset(Permission),          # 전체
}


class UserStatus(StrEnum):
    """계정 생애주기 (D-014). is_active 불리언으로는 '승인 대기'와 '비활성화'를 구분할 수 없다."""
    PENDING = "pending"      # 가입 신청됨 — 로그인 불가
    ACTIVE = "active"        # 승인됨
    DISABLED = "disabled"    # 관리자가 비활성화 — 로그인 불가
    REJECTED = "rejected"    # 가입 거부됨 — 로그인 불가


#: 로그인이 허용되는 상태는 하나뿐이다. 새 상태를 추가할 때 이 집합을 반드시 검토한다.
LOGIN_ALLOWED: frozenset[UserStatus] = frozenset({UserStatus.ACTIVE})


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: UUID
    username: str
    display_name: str | None
    role: Role
    status: UserStatus
    must_change_password: bool = False   # 관리자가 임시 비밀번호를 발급한 경우 (FR-1008)

    def has(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]

    @property
    def can_sign_in(self) -> bool:
        return self.status in LOGIN_ALLOWED
```

### 2.1 상태 전이 (FR-1006·1007, D-014)

```
        가입 신청
            │
            ▼
        ┌─────────┐   승인(역할·범위 부여)   ┌────────┐   비활성화   ┌──────────┐
        │ pending │ ──────────────────────► │ active │ ───────────► │ disabled │
        └─────────┘                         └────────┘ ◄─────────── └──────────┘
            │                                             재활성화
            │ 거부
            ▼
        ┌──────────┐
        │ rejected │
        └──────────┘
```

```python
ALLOWED_TRANSITIONS: dict[UserStatus, frozenset[UserStatus]] = {
    UserStatus.PENDING: frozenset({UserStatus.ACTIVE, UserStatus.REJECTED}),
    UserStatus.ACTIVE: frozenset({UserStatus.DISABLED}),
    UserStatus.DISABLED: frozenset({UserStatus.ACTIVE}),
    UserStatus.REJECTED: frozenset(),          # 종료 상태 — 재신청은 새 계정으로
}
```

**계정을 삭제하지 않는다** (D-014). 감사 로그(FR-1004)의 행위자 참조가 끊기면 추적이 불가능해진다.
관리자 화면의 "삭제"는 `disabled` 전환이며, 문구도 "비활성화"로 표기한다.

| 역할 | 자원 조회 | 메타데이터 | 내보내기 | 수동 수집 | 연결 관리 | 사용자 관리 | 감사 조회 |
|---|---|---|---|---|---|---|---|
| `viewer` | 범위 내 | ✗ | 범위 내 | ✗ | ✗ | ✗ | ✗ |
| `operator` | 범위 내 | ✓ | 범위 내 | ✗ | ✗ | ✗ | ✗ |
| `admin` | 전체 | ✓ | 전체 | ✓ | ✓ | ✓ | ✓ |

**연결 관리는 관리자 전용이다** (NFR-210). 자격증명을 다루기 때문이다.

---

## 3. 조회 범위 (FR-1003) — 핵심 통제

```python
@dataclass(frozen=True, slots=True)
class AccessScope:
    """사용자의 조회 범위. 모든 조회 쿼리에 적용된다."""
    user_id: UUID
    username: str
    role: Role
    allowed_connection_ids: frozenset[UUID] | None    # None = 전체 (admin만)

    @property
    def is_unrestricted(self) -> bool:
        return self.allowed_connection_ids is None

    @property
    def is_empty(self) -> bool:
        """범위가 비어 있으면 아무것도 볼 수 없다 (기본 거부)."""
        return self.allowed_connection_ids is not None and not self.allowed_connection_ids

    def can_access(self, connection_id: UUID) -> bool:
        return self.is_unrestricted or connection_id in (self.allowed_connection_ids or frozenset())

    def require(self, permission: Permission) -> None:
        if permission not in ROLE_PERMISSIONS[self.role]:
            raise PermissionError(f"권한이 없습니다: {permission.value}")


def build_scope(user: AuthenticatedUser, connection_ids: Sequence[UUID]) -> AccessScope:
    return AccessScope(
        user_id=user.user_id, username=user.username, role=user.role,
        allowed_connection_ids=None if user.role is Role.ADMIN else frozenset(connection_ids),
    )
```

### 3.1 적용 원칙

| 원칙 | 이유 |
|---|---|
| **SQL 단계에서 적용** | 조회 후 파이썬에서 거르면 페이징 건수가 어긋나고 누락된다 |
| **유스케이스 계층에서 적용** | API에만 두면 리포트·내보내기·외부 API·대시보드에서 우회된다 |
| **저장소 메서드가 `scope`를 필수 인자로** | 선택 인자면 빠뜨리기 쉽다 |
| **범위 없는 전체 조회 함수를 만들지 않음** | 존재하면 언젠가 쓰인다 |

```python
# 저장소 시그니처 — scope가 첫 필수 인자
async def search_vms(
    self, scope: AccessScope, criteria: SearchCriteria, page: Page
) -> PagedResult[VmSummary]: ...
```

### 3.2 SQL 바인딩 패턴

```python
def scope_params(scope: AccessScope) -> dict[str, Any]:
    return {
        "scope_all": scope.is_unrestricted,
        "scope_connection_ids": list(scope.allowed_connection_ids or []),
    }
```

```sql
AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
```

**`is_empty`인 스코프**는 `ANY('{}')`가 되어 아무것도 매칭되지 않는다 — 기본 거부가 자연스럽게 성립한다.

### 3.3 조기 반환 최적화

```python
async def search_vms(self, scope, criteria, page):
    if scope.is_empty:
        return PagedResult(items=(), total=0, page=page)     # DB 조회 불필요
    ...
```

---

## 4. 인증 (FR-1001)

### 4.1 비밀번호 해시 (`security/password.py`)

```python
from passlib.context import CryptContext

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(plain: SecretStr) -> str:
    return _ctx.hash(plain.get_secret_value())


def verify_password(plain: SecretStr, hashed: str) -> tuple[bool, str | None]:
    """검증 결과와, 재해시가 필요하면 새 해시를 반환한다."""
    ok = _ctx.verify(plain.get_secret_value(), hashed)
    if ok and _ctx.needs_update(hashed):
        return True, _ctx.hash(plain.get_secret_value())
    return ok, None
```

**bcrypt는 72바이트 초과 입력을 잘라낸다.** 긴 비밀번호를 허용하려면 입력 길이를 제한하거나
사전 해시가 필요하다. 단순히 **최대 72자로 제한**하고 UI에서 안내한다.

### 4.2 JWT (`security/tokens.py`)

```python
from jose import jwt, JWTError

ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str                # user_id
    username: str
    role: Role
    exp: int
    iat: int
    jti: str                # 토큰 고유 ID (무효화 대비)


def create_access_token(user: AuthenticatedUser, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.user_id),
        "username": user.username,
        "role": user.role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    try:
        raw = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[ALGORITHM])
    except JWTError:
        raise AuthenticationError("토큰이 유효하지 않습니다.") from None
    return TokenClaims(...)
```

**조회 범위를 토큰에 넣지 않는다.** 범위 변경이 토큰 만료까지 지연되면 권한 회수가 늦어진다.
요청마다 DB에서 조회하되, 짧은 TTL(30초) 캐시로 부하를 줄인다.

### 4.3 로그인 보호

```python
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


STATUS_MESSAGE = {
    UserStatus.PENDING: "가입 신청이 검토 중입니다. 관리자 승인 후 로그인할 수 있습니다.",
    UserStatus.REJECTED: "가입이 승인되지 않았습니다. 관리자에게 문의하세요.",
    UserStatus.DISABLED: "비활성화된 계정입니다. 관리자에게 문의하세요.",
}


class AuthService:
    async def login(self, username: str, password: SecretStr, ip: str | None) -> str:
        user_row = await self._users.find_by_username(username)

        # 계정 열거 방지: 계정 없음과 비밀번호 불일치를 구분하지 않는다
        if user_row is None:
            await self._fake_verify()                    # 타이밍 공격 완화
            await self._audit_failure(username, ip, "invalid_credentials")
            raise AuthenticationError("아이디 또는 비밀번호가 올바르지 않습니다.")

        if user_row.locked_until and user_row.locked_until > datetime.now(UTC):
            await self._audit_failure(username, ip, "locked")
            raise AuthenticationError("계정이 일시적으로 잠겨 있습니다. 잠시 후 다시 시도하세요.")

        # 비밀번호를 먼저 검증한다 — 상태 검사보다 앞이다 (D-014 §4번 근거)
        ok, new_hash = verify_password(password, user_row.password_hash)
        if not ok:
            await self._register_failure(user_row)
            await self._audit_failure(username, ip, "invalid_credentials")
            raise AuthenticationError("아이디 또는 비밀번호가 올바르지 않습니다.")

        # 비밀번호가 맞은 뒤에야 상태별 사유를 알려준다.
        # 순서를 바꾸면 비밀번호를 모르는 사람도 계정 존재를 알아낼 수 있다.
        if user_row.status not in LOGIN_ALLOWED:
            await self._audit_failure(username, ip, f"status_{user_row.status}")
            raise AuthenticationError(STATUS_MESSAGE[UserStatus(user_row.status)])

        if new_hash:
            await self._users.update_hash(user_row.id, new_hash)
        await self._users.reset_failures(user_row.id)
        await self._audit_success(user_row, ip)
        return create_access_token(_to_user(user_row), self._settings)

    async def _fake_verify(self) -> None:
        """존재하지 않는 계정에도 해시 검증 시간을 소비하여 타이밍 차이를 줄인다."""
        _ctx.verify("dummy", _DUMMY_HASH)
```

**응답 메시지를 통일한다.** "계정 없음"과 "비밀번호 불일치"를 구분하면 계정 열거가 가능해진다.

**상태 사유는 비밀번호 검증을 통과한 뒤에만 노출한다.** 이 순서가 계정 열거 방지와
"왜 로그인이 안 되는지 모르겠다"는 사용성 문제를 동시에 해결한다.

### 4.3.1 토큰 전달 — `httpOnly` 쿠키 (D-014)

```python
COOKIE_NAME = "portal_session"


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,                       # JS에서 접근 불가 — XSS 시 토큰 유출 차단
        samesite="strict",                   # CSRF 기본 방어
        secure=settings.cookie_secure,       # 운영 True. 개발 HTTP에서는 False
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
```

- **`localStorage`에 토큰을 저장하지 않는다.** 인벤토리 정보의 민감도(NFR-206)를 고려하면
  XSS 한 번에 토큰이 전량 유출되는 방식은 위험이 크다.
- 로그아웃은 쿠키 삭제로 처리한다. 서버측 무효화(`jti` 블랙리스트)는 Step 8 API 키 도입 시 함께 검토한다.
- 외부 연동(FR-1104)은 쿠키가 아니라 **API 키 헤더**를 쓴다. 브라우저 세션과 경로를 분리한다.

---

## 4.5 가입 신청 (FR-1006)

```python
class RegistrationService:
    async def register(self, req: RegisterRequest, ip: str | None) -> None:
        """가입을 신청한다. 반환값이 없다 — 성공·중복을 구분하지 않기 위함이다."""
        normalized = req.username.strip().lower()

        existing = await self._users.find_by_username(normalized)
        if existing is not None:
            # 중복이어도 같은 응답을 준다. 다르면 가입 폼이 계정 열거 수단이 된다.
            await self._audit(AuditAction.USER_REGISTER_DUPLICATE, normalized, ip)
            return

        await self._users.create(
            username=normalized,
            password_hash=hash_password(req.password),
            display_name=req.display_name,
            email=req.email,
            role=Role.VIEWER,                # 승인 시 관리자가 다시 정한다
            status=UserStatus.PENDING,       # 신청 상태로만 저장 (D-014)
        )
        await self._audit(AuditAction.USER_REGISTER, normalized, ip)
```

**API는 항상 202를 반환하고 "신청이 접수되었습니다"만 응답한다.**
중복 여부를 알려주면 가입 폼으로 계정 목록을 확인할 수 있다.

| 검증 | 규칙 |
|---|---|
| 아이디 | 3~64자, 영문 소문자·숫자·`.`·`_`·`-`. 저장 시 소문자 정규화 |
| 비밀번호 | 10~72자 (bcrypt 절단 한계, §4.1) |
| 표시 이름 | 1~64자 |
| 이메일 | 형식 검증만. 인증 메일은 보내지 않는다 (D-014 미결) |

> **가입 API에 호출량 제한을 적용한다.** 제한이 없으면 계정 테이블을 무한히 채울 수 있다.
> IP당 분당 5회를 기본값으로 하고 설정으로 조정한다.

---

## 4.6 사용자 관리 (FR-1007)

```python
class UserAdminService:
    """모든 메서드가 admin 권한을 요구한다. 호출 전 scope.require(Permission.USER_MANAGE)."""

    async def approve(
        self, actor: AccessScope, user_id: UUID, role: Role, connection_ids: Sequence[UUID]
    ) -> None:
        """가입을 승인하면서 역할과 조회 범위를 함께 부여한다.

        범위를 비워 두면 아무것도 보이지 않는다 (기본 거부, §3). 이는 정상 동작이다.
        """
        actor.require(Permission.USER_MANAGE)
        user = await self._require_user(user_id)
        self._check_transition(user.status, UserStatus.ACTIVE)

        await self._users.update_status(user_id, UserStatus.ACTIVE, approved_by=actor.username)
        await self._users.set_role(user_id, role)
        await self._scopes.replace(user_id, connection_ids)
        await self._audit(actor, AuditAction.USER_APPROVE, user_id,
                          detail={"role": role.value, "connection_count": len(connection_ids)})

    async def reject(self, actor: AccessScope, user_id: UUID, reason: str | None) -> None: ...
    async def disable(self, actor: AccessScope, user_id: UUID) -> None: ...
    async def enable(self, actor: AccessScope, user_id: UUID) -> None: ...
    async def change_role(self, actor: AccessScope, user_id: UUID, role: Role) -> None: ...
    async def set_scopes(self, actor: AccessScope, user_id: UUID, ids: Sequence[UUID]) -> None: ...
    async def reset_password(self, actor: AccessScope, user_id: UUID) -> str:
        """임시 비밀번호를 생성해 반환한다. 반환값은 화면에 1회만 표시한다 (FR-1008)."""

    def _check_transition(self, current: UserStatus, target: UserStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[current]:
            raise ValidationError(f"{current} → {target} 상태 전이는 허용되지 않습니다.")
```

### 4.6.1 마지막 관리자 보호

```python
async def _guard_last_admin(self, user_id: UUID, *, becoming: Role | UserStatus) -> None:
    """마지막 활성 관리자의 강등·비활성화를 막는다.

    막지 않으면 관리자가 0명이 되어 아무도 승인·연결 관리를 할 수 없는 잠금 상태가 된다.
    이때는 DB를 직접 고치는 수밖에 없다.
    """
    if await self._users.count_active_admins(exclude=user_id) == 0:
        raise ValidationError("마지막 관리자입니다. 다른 관리자를 지정한 뒤에 변경하세요.")
```

**자기 자신의 역할 변경·비활성화도 막는다.** 실수로 스스로를 잠그는 것이 가장 흔한 사고다.

### 4.6.2 임시 비밀번호 (FR-1008)

- 관리자가 발급하면 `must_change_password = true`가 되고, **첫 로그인 후 비밀번호 변경 전까지
  조회 API를 호출할 수 없다.**
- 생성한 평문은 **응답에 1회만 실어 보내고 저장하지 않는다.** 감사 로그에도 남기지 않는다 (값이 아니라 "발급됨" 사실만).
- 본인 비밀번호 변경은 **현재 비밀번호 확인**을 요구한다.

### 4.4 외부 인증 (FR-1005) — `[TODO]`

AD/LDAP/SSO 필요 여부 미확정. 도입에 대비해 인터페이스로 분리한다.

```python
class AuthProvider(Protocol):
    async def authenticate(self, username: str, password: SecretStr) -> ExternalIdentity | None: ...


class LocalAuthProvider:
    """DB의 password_hash로 검증한다."""


# 향후: LdapAuthProvider — 자격증명 검증만 위임
```

**사용자 저장소(역할·범위)는 외부 인증과 무관하게 포탈이 관리한다.**
LDAP은 "누구인가"만 답하고, "무엇을 볼 수 있는가"는 포탈이 결정한다.

---

## 5. DB 스키마

```sql
CREATE TABLE users (
    user_id            UUID PRIMARY KEY,
    username           TEXT UNIQUE NOT NULL,       -- 소문자 정규화하여 저장
    password_hash      TEXT,                       -- 외부 인증 사용자는 NULL
    display_name       TEXT,
    email              TEXT,
    role               TEXT NOT NULL DEFAULT 'viewer',
    auth_provider      TEXT NOT NULL DEFAULT 'local',
    -- 계정 생애주기 (D-014). is_active 불리언이 아니다 —
    -- '승인 대기'와 '비활성화'를 구분해야 한다.
    status             TEXT NOT NULL DEFAULT 'pending',
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    reject_reason      TEXT,
    must_change_password BOOLEAN NOT NULL DEFAULT false,   -- 임시 비밀번호 발급 시 (FR-1008)
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       TIMESTAMPTZ,
    last_login_at      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_users_status CHECK (status IN ('pending', 'active', 'disabled', 'rejected'))
);

-- 관리자 화면의 첫 화면이 '승인 대기 목록'이므로 부분 인덱스를 둔다
CREATE INDEX idx_users_pending ON users (created_at) WHERE status = 'pending';
CREATE INDEX idx_users_status ON users (status);

CREATE TABLE user_connection_scopes (
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES connections(connection_id) ON DELETE CASCADE,
    granted_by    TEXT,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, connection_id)
);
CREATE INDEX idx_scopes_user ON user_connection_scopes (user_id);

CREATE TABLE api_keys (                            -- 외부 연동용 (계획 08 §4)
    key_id        UUID PRIMARY KEY,
    name          TEXT NOT NULL,
    key_hash      TEXT NOT NULL,                   -- 원문은 발급 시 1회만 노출
    owner_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    is_active     BOOLEAN NOT NULL DEFAULT true,
    expires_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_api_keys_hash ON api_keys (key_hash);
```

**`user_connection_scopes`에 행이 없는 `viewer`/`operator`는 아무것도 볼 수 없다** (기본 거부).
`admin`은 이 테이블과 무관하게 전체를 본다.

**연결 삭제 시 `ON DELETE CASCADE`로 범위도 정리된다.** 남아 있으면 나중에 같은 UUID가 재사용될 때 문제가 된다(실제로는 UUID라 충돌 가능성은 낮지만 데이터 정합성 차원).

---

## 6. API 계층 연동 (`src/api/deps.py`)

```python
async def get_current_user(
    request: Request,
    api_key: str | None = Security(api_key_header),      # 외부 연동용 (Step 8)
    users: UserRepository = Depends(get_user_repo),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    if api_key:
        return await _authenticate_api_key(api_key, users)

    token = request.cookies.get(COOKIE_NAME)             # 브라우저 세션은 쿠키다 (D-014)
    if not token:
        raise HTTPException(401, "인증이 필요합니다.")

    claims = decode_access_token(token, settings)
    user = await users.get(UUID(claims.sub))

    # 상태를 매 요청 재확인한다. 토큰이 서명상 유효해도 계정이 이미
    # 비활성화·거부되었을 수 있다. 이 검사가 없으면 권한 회수가 토큰 만료까지 지연된다.
    if user is None or not user.can_sign_in:
        raise HTTPException(401, "세션이 유효하지 않습니다. 다시 로그인하세요.")
    return user


async def get_access_scope(
    user: AuthenticatedUser = Depends(get_current_user),
    scopes: ScopeRepository = Depends(get_scope_repo),
) -> AccessScope:
    """요청마다 DB에서 범위를 조회한다 (토큰에 넣지 않음). 30초 캐시."""
    conn_ids = await scopes.list_for_user(user.user_id)
    return build_scope(user, conn_ids)


def require(*permissions: Permission):
    """라우터용 권한 검사 의존성."""
    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        for p in permissions:
            if not user.has(p):
                raise HTTPException(403, f"권한이 없습니다: {p.value}")
        return user
    return _check
```

### 6.1 이중 방어

```python
# API 라우터
@router.post("/connections", dependencies=[Depends(require(Permission.CONNECTION_MANAGE))])
async def create_connection(...): ...

# 유스케이스에서도 재검사
class ConnectionService:
    async def create(self, scope: AccessScope, ...) -> Connection:
        scope.require(Permission.CONNECTION_MANAGE)     # 워커·스크립트 경로 방어
        ...
```

API만 신뢰하면 워커·리포트·CLI 경로에서 우회된다.

---

## 7. 관리자 부트스트랩

```python
async def ensure_bootstrap_admin(settings: Settings, users: UserRepository) -> None:
    """최초 기동 시 관리자 계정을 생성한다.

    하드코딩된 기본 비밀번호를 두지 않는다. 환경변수가 없으면 생성하지 않고 경고만 남긴다.
    """
    if await users.count() > 0:
        return
    if not settings.bootstrap_admin_username or not settings.bootstrap_admin_password:
        logger.warning(
            "관리자 계정이 없습니다. PORTAL_BOOTSTRAP_ADMIN_USERNAME/PASSWORD를 설정하고 재기동하세요."
        )
        return
    await users.create(
        username=settings.bootstrap_admin_username,
        password_hash=hash_password(settings.bootstrap_admin_password),
        role=Role.ADMIN, display_name="Administrator",
    )
    logger.info("부트스트랩 관리자 계정을 생성했습니다.")
```

**부트스트랩 후 환경변수를 제거하도록 안내**한다. 로그에 사용자명만 남기고 비밀번호는 남기지 않는다.

---

## 8. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `Role`·`Permission`·`ROLE_PERMISSIONS` | 역할별 권한 매트릭스 대조 |
| 2 | `AccessScope` + `build_scope` | `is_empty` 기본 거부, admin 무제한 |
| 3 | 스키마 마이그레이션 | FK CASCADE 동작 |
| 4 | `password.py` | bcrypt 왕복, 72자 초과 처리, `needs_update` |
| 5 | `tokens.py` | 만료·서명 위조 거부, 클레임 파싱 |
| 6 | `AuthService.login` | 실패 5회 잠금, 성공 시 카운터 초기화, **메시지 통일** |
| 7 | 타이밍 완화 (`_fake_verify`) | 존재/미존재 계정 응답 시간 차이 측정 |
| 8 | `deps.py` | 401/403 분기, 범위 주입 |
| 9 | `require()` + 유스케이스 재검사 | 이중 방어 |
| 10 | API 키 인증 | 해시 저장, 만료, `last_used_at` 갱신 |
| 11 | 부트스트랩 | 계정 없을 때만 생성, 비밀번호 미로깅 |

## 9. 완료 기준

- [ ] 역할별 권한이 §2 매트릭스대로 동작
- [ ] `viewer`가 범위 밖 연결의 자원을 **목록·상세·검색·관계탐색·내보내기·대시보드 집계** 어디서도 볼 수 없음
- [ ] 저장소 조회 메서드가 `scope`를 필수 인자로 받음 (선택 인자 없음)
- [ ] 범위 필터가 SQL에 적용됨 (`EXPLAIN`으로 확인)
- [ ] 범위가 빈 사용자는 DB 조회 없이 빈 결과 반환
- [ ] 연결 관리 API가 `admin`에게만 허용됨
- [ ] 로그인 실패 응답이 계정 존재 여부를 노출하지 않음
- [ ] 존재/미존재 계정의 응답 시간 차이가 유의하지 않음
- [ ] 5회 실패 후 계정 잠금, 성공 시 카운터 초기화
- [ ] 로그인 성공·실패가 감사 로그에 기록됨
- [ ] JWT에 조회 범위가 포함되지 않음
- [ ] 하드코딩된 기본 관리자 비밀번호가 없음
- [ ] **`pending`·`rejected`·`disabled` 계정이 로그인하지 못함** (비밀번호가 맞아도)
- [ ] **상태 사유가 비밀번호 검증을 통과한 뒤에만 노출됨** — 틀린 비밀번호로는 계정 존재를 알 수 없음
- [ ] **가입 폼이 아이디 중복 여부를 노출하지 않음** (중복이어도 동일 응답)
- [ ] 승인 시 부여한 역할·범위가 즉시 적용됨 (재로그인 불필요 — 범위는 요청마다 조회)
- [ ] 범위를 비운 채 승인하면 자원이 하나도 보이지 않음 (기본 거부)
- [ ] **마지막 활성 관리자를 강등·비활성화할 수 없음**
- [ ] 자기 자신의 역할 변경·비활성화가 차단됨
- [ ] 허용되지 않은 상태 전이가 거부됨 (`rejected` → `active` 등)
- [ ] 임시 비밀번호가 응답에 1회만 나오고 **감사 로그·DB에 평문이 없음**
- [ ] `must_change_password` 사용자가 비밀번호 변경 전에는 조회 API를 호출할 수 없음
- [ ] 세션 쿠키가 `httpOnly`·`SameSite=Strict`로 설정됨
- [ ] **JS에서 `document.cookie`로 토큰을 읽을 수 없음**
- [ ] **비활성화 직후 기존 토큰으로 API를 호출하면 401** (토큰 만료를 기다리지 않음)
- [ ] 계정이 물리 삭제되지 않음 (감사 로그 행위자 참조 유지)
- [ ] `/security-review` 실행 결과 권한 우회 지적 0건

## 10. 주의사항

- **범위 필터 누락이 이 계획에서 가장 흔한 결함이다.** 새 조회 경로(리포트, 내보내기, 외부 API, 대시보드 집계, 변경 이력)를 만들 때마다 확인한다. verifier의 필수 검증 항목이다.
- **대시보드 집계도 범위를 반영해야 한다.** 전체 VM 수를 보여주면 범위 밖 정보가 누설된다.
- JWT에 범위를 넣으면 권한 회수가 토큰 만료까지 지연된다.
- bcrypt의 72바이트 절단을 인지하고 입력 길이를 제한한다.
- 계정 열거를 막으려면 메시지 통일뿐 아니라 **응답 시간**도 맞춰야 한다.
- API 키는 발급 시 1회만 원문을 노출하고 이후에는 해시만 보관한다.
