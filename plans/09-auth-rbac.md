# 09. 인증·권한·조회 범위 제한

> Wave: 2
> 계층: domain (`src/domain/auth.py`) · infrastructure (`src/infrastructure/security/`)
> 담당 요건: FR-1001~1003, FR-1005, NFR-204·210
> 의존: 01, 02, 10
> 관련 결정: D-005

## 1. 목적

포탈 사용자를 인증하고, 역할과 조회 범위로 접근을 제한한다.

**인벤토리 정보(IP·호스트명·OS) 자체가 민감 정보**이므로(NFR-206), 조회 권한 제한이 이 프로젝트의 핵심 보안 통제다.

## 2. 역할 모델 (FR-1002)

| 역할 | 자원 조회 | 메타데이터 편집 | 연결 관리 | 사용자 관리 | 내보내기 |
|---|---|---|---|---|---|
| `viewer` (조회자) | 범위 내 | ✗ | ✗ | ✗ | 범위 내 |
| `operator` (운영자) | 범위 내 | ✓ | ✗ | ✗ | 범위 내 |
| `admin` (관리자) | 전체 | ✓ | ✓ | ✓ | 전체 |

```python
class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

class Permission(StrEnum):
    RESOURCE_READ = "resource.read"
    METADATA_WRITE = "metadata.write"
    CONNECTION_MANAGE = "connection.manage"
    USER_MANAGE = "user.manage"
    EXPORT = "export"
    COLLECTION_TRIGGER = "collection.trigger"   # 수동 수집 (FR-202)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {...}
```

**연결 관리는 관리자 전용이다** (NFR-210). 자격증명을 다루기 때문이다.

## 3. 조회 범위 (FR-1003) — 핵심 통제

사용자가 볼 수 있는 자원을 **연결 단위**로 제한한다.

```python
@dataclass(frozen=True)
class AccessScope:
    """사용자의 조회 범위. 모든 조회 쿼리에 적용된다."""
    user_id: UUID
    role: Role
    allowed_connection_ids: frozenset[UUID] | None   # None = 전체 (admin만)

    def is_unrestricted(self) -> bool:
        return self.allowed_connection_ids is None
```

### 3.1 적용 원칙

- **범위 필터는 SQL 단계에서 적용한다.** 조회 후 파이썬에서 거르면 페이징 건수가 어긋나고 누락 위험이 있다
- **유스케이스 계층에서 적용한다.** API 계층에만 두면 다른 진입점(리포트, 내보내기, 외부 API)에서 우회된다
- 범위 없는 전체 조회 함수를 만들지 않는다. 저장소 메서드가 `scope`를 **필수 인자**로 받게 한다

```python
# 저장소 시그니처 — scope가 선택 인자면 빠뜨리기 쉽다
async def search_virtual_machines(
    self, scope: AccessScope, criteria: SearchCriteria, page: Page
) -> PagedResult[VirtualMachine]: ...
```

### 3.2 범위 확장 여지

연결 단위 제한으로 시작한다. 자원 그룹·태그 단위 제한이 필요해지면 `AccessScope`에 필드를 추가한다.
`[TODO]` 세분화 필요 여부는 운영 후 판단.

## 4. 인증 (FR-1001)

### 4.1 로컬 계정 (Phase 1)

- 비밀번호 해시: `passlib` + bcrypt
- JWT 발급: `python-jose`, HS256, 만료 기본 8시간(`jwt_expire_minutes`)
- 토큰 클레임: `sub`(user_id), `role`, `exp`, `iat`
- **조회 범위는 토큰에 넣지 않는다.** 범위 변경이 즉시 반영되도록 요청마다 DB에서 조회한다 (또는 짧은 TTL 캐시)

### 4.2 로그인 보호

- 로그인 실패 횟수 제한 (계정별, 기본 5회 → 일시 잠금)
- 실패·성공 모두 감사 로그 기록 (계획 10 §3.1)
- **응답에서 "계정 없음"과 "비밀번호 불일치"를 구분하지 않는다** (계정 열거 방지)

### 4.3 외부 인증 연동 (FR-1005) — `[TODO]`

AD/LDAP/SSO 필요 여부가 미확정이다. 도입 대비:

```python
class AuthProvider(Protocol):
    async def authenticate(self, username: str, password: SecretStr) -> AuthenticatedUser | None: ...
```

로컬 구현을 이 Protocol로 감싸 두면 LDAP 구현체 추가로 대응할 수 있다.
**사용자 저장소(역할·범위)는 외부 인증과 무관하게 포탈이 관리**한다.

## 5. DB 스키마

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,                 -- 외부 인증 사용자는 NULL
    display_name TEXT,
    role TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    failed_login_count INT NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    last_login_at TIMESTAMPTZ
);

CREATE TABLE user_connection_scopes (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, connection_id)
);
```

`user_connection_scopes`에 행이 없는 `viewer`/`operator`는 **아무것도 볼 수 없다**(기본 거부).
`admin`은 이 테이블과 무관하게 전체를 본다.

## 6. API 계층 연동 (계획 08에서 사용)

```python
# src/api/deps.py
async def get_current_user(token: str = Depends(oauth2_scheme)) -> AuthenticatedUser: ...
async def get_access_scope(user = Depends(get_current_user)) -> AccessScope: ...
def require(permission: Permission) -> Callable: ...   # 권한 검사 의존성
```

**이중 방어**: API 라우터에 `require(...)`를 걸고, 유스케이스에서도 권한을 검사한다.
API만 신뢰하면 워커·리포트 경로에서 우회된다.

## 7. 구현 순서

1. `Role`·`Permission`·`AccessScope` 도메인 모델 → 검증: 역할별 권한 매핑 테스트
2. 사용자 저장소 + 스키마 마이그레이션 → 검증: CRUD
3. 비밀번호 해시·검증 → 검증: bcrypt 왕복, 잘못된 비밀번호 거부
4. JWT 발급·검증 → 검증: 만료·서명 위조 거부
5. 로그인 실패 제한 → 검증: 5회 실패 후 잠금, 성공 시 카운터 초기화
6. `AuthProvider` Protocol + 로컬 구현 → 검증: Protocol 만족
7. API 의존성 (`deps.py`) → 검증: 권한 없는 요청 403

## 8. 완료 기준

- [ ] 역할별 권한이 매트릭스(§2)대로 동작
- [ ] `viewer`가 범위 밖 연결의 자원을 목록·상세·검색·내보내기 어디서도 볼 수 없음
- [ ] 저장소 조회 메서드가 `scope`를 필수 인자로 받음 (선택 인자 없음)
- [ ] 범위 필터가 SQL에 적용됨 (`EXPLAIN`으로 확인)
- [ ] 연결 관리 API가 `admin`에게만 허용됨
- [ ] 로그인 실패 응답이 계정 존재 여부를 노출하지 않음
- [ ] 로그인 성공·실패가 감사 로그에 기록됨
- [ ] `/security-review` 실행 결과 권한 우회 지적 0건

## 9. 주의사항

- **범위 필터 누락이 이 계획에서 가장 흔한 결함이다.** 새 조회 경로(리포트, 내보내기, 외부 API, 대시보드 집계)를 만들 때마다 범위 적용을 확인한다. verifier의 필수 검증 항목이다.
- 대시보드 집계(FR-9xx)도 범위를 반영해야 한다. 전체 VM 수를 보여주면 범위 밖 정보가 누설된다.
- JWT에 조회 범위를 넣으면 권한 회수가 토큰 만료까지 지연된다. 넣지 않는다.
- `admin` 계정의 초기 생성 방법을 정한다(환경변수 기반 부트스트랩 권장). 하드코딩된 기본 비밀번호를 두지 않는다.
