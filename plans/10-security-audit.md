# 10. 자격증명 보호 및 감사 로그

> Wave: 1 · 계층: infrastructure (`security/`) · domain (`audit.py`)
> 담당 요건: NFR-203·205·206·208·209, FR-104·108, FR-1004
> 의존: 01, 02 · 관련 결정: D-008

## 1. 목적

하이퍼바이저 접속 자격증명을 안전하게 저장·사용하고, 관리 작업의 감사 추적을 남긴다.

**통합 포탈은 다수 vCenter/Hyper-V의 자격증명을 한곳에 모으므로, 이 계획의 결함은 조직 전체의 침해 지점이 된다.**

## 2. 모듈 구성

```
src/infrastructure/security/
├── __init__.py
├── keys.py           KeyProvider — 암호화 키 조회
├── cipher.py         CredentialCipher — AES-256-GCM
├── masking.py        로그 마스킹 필터, 에러 메시지 정제
├── password.py       사용자 비밀번호 해시 (계획 09에서 사용)
└── tokens.py         JWT 발급·검증 (계획 09에서 사용)
```

---

## 3. 자격증명 암호화 (`cipher.py`)

### 3.1 저장 형식

```
{key_version}${base64url(nonce)}${base64url(ciphertext_with_tag)}
예: 1$k3Jd8fLp2Qw9xYzA$8fH2...==
```

**`key_version` 접두사가 필수다.** 키 교체 시 어느 키로 복호화할지 알아야 하며,
버전 없이 저장하면 교체가 불가능해진다.

### 3.2 구현

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CIPHER_SEPARATOR = "$"
NONCE_SIZE = 12                       # AES-GCM 권장


class CredentialCipher:
    """자격증명 암복호. 키는 KeyProvider가 공급하며 코드·DB에 평문으로 두지 않는다."""

    def __init__(self, keys: KeyProvider) -> None:
        self._keys = keys

    def encrypt(self, plaintext: SecretStr) -> str:
        version = self._keys.current_version
        key = self._keys.get_key(version)
        nonce = os.urandom(NONCE_SIZE)
        ct = AESGCM(key).encrypt(nonce, plaintext.get_secret_value().encode("utf-8"), None)
        return CIPHER_SEPARATOR.join([
            str(version), _b64(nonce), _b64(ct),
        ])

    def decrypt(self, stored: str) -> SecretStr:
        try:
            version_s, nonce_b64, ct_b64 = stored.split(CIPHER_SEPARATOR, 2)
            key = self._keys.get_key(int(version_s))
            plain = AESGCM(key).decrypt(_unb64(nonce_b64), _unb64(ct_b64), None)
        except (ValueError, KeyError, InvalidTag) as exc:
            # 실패 사유를 상세히 노출하지 않는다 (오라클 공격 방지)
            raise PortalError("자격증명을 복호화할 수 없습니다.") from None
        return SecretStr(plain.decode("utf-8"))

    def needs_rotation(self, stored: str) -> bool:
        try:
            return int(stored.split(CIPHER_SEPARATOR, 1)[0]) != self._keys.current_version
        except (ValueError, IndexError):
            return True
```

**AES-GCM을 쓰는 이유**: 인증 태그가 포함되어 변조를 감지한다. CBC 등 인증 없는 모드는 변조를 놓친다.

### 3.3 키 제공자 (`keys.py`) — NFR-208 `[TODO]`

키 관리 방식이 미확정이므로 **인터페이스로 분리**하여 나중에 구현체만 교체한다.

```python
class KeyProvider(Protocol):
    def get_key(self, version: int) -> bytes: ...
    @property
    def current_version(self) -> int: ...
    def known_versions(self) -> Sequence[int]: ...


class EnvKeyProvider:
    """환경변수 기반 (임시 기본 구현).

    PORTAL_CREDENTIAL_ENCRYPTION_KEY       현재 키 (base64, 32바이트)
    PORTAL_CREDENTIAL_KEY_VERSION          현재 버전 (기본 1)
    PORTAL_CREDENTIAL_ENCRYPTION_KEY_V{n}  구버전 키 (교체 중에만 필요)
    """

    def __init__(self, settings: Settings) -> None:
        self._keys: dict[int, bytes] = {}
        current = base64.b64decode(settings.credential_encryption_key.get_secret_value())
        if len(current) != 32:
            raise ValueError("암호화 키는 32바이트(base64)여야 합니다.")
        self._current = settings.credential_key_version
        self._keys[self._current] = current
        for v, raw in settings.credential_legacy_keys.items():
            self._keys[int(v)] = base64.b64decode(raw)

    def get_key(self, version: int) -> bytes:
        if version not in self._keys:
            raise KeyError(f"키 버전 {version}을 찾을 수 없습니다.")
        return self._keys[version]
```

> `settings.credential_legacy_keys`는 `dict[str, str]`이므로 `.env`에 **JSON 형식**으로 써야 한다
> (CLAUDE.md Known Mistakes 1번): `PORTAL_CREDENTIAL_LEGACY_KEYS={"1":"base64key..."}`

### 3.4 키 교체 스크립트

**처음부터 만들어 둔다.** 나중에 넣으려면 운영 중인 데이터를 다뤄야 해서 위험이 커진다.

```python
# scripts/rotate_credential_key.py
async def rotate(dry_run: bool = True) -> RotationSummary:
    """구버전 키로 암호화된 자격증명을 현재 키로 재암호화한다."""
    cipher = CredentialCipher(EnvKeyProvider(settings))
    rows = await repo.list_all_connections_raw()
    targets = [r for r in rows if cipher.needs_rotation(r.password_encrypted)]
    logger.info("재암호화 대상", extra={"total": len(rows), "targets": len(targets)})

    if dry_run:
        return RotationSummary(total=len(rows), rotated=0, dry_run=True)

    rotated = 0
    for row in targets:
        async with session.begin():                     # 레코드 단위 트랜잭션
            plain = cipher.decrypt(row.password_encrypted)
            await repo.update_password_raw(row.connection_id, cipher.encrypt(plain))
            rotated += 1
        logger.info("재암호화 완료", extra={"connection_id": str(row.connection_id),
                                          "progress": f"{rotated}/{len(targets)}"})
    return RotationSummary(total=len(rows), rotated=rotated, dry_run=False)
```

- 기본이 `dry_run=True`다. 실수로 전체를 건드리지 않게 한다
- 레코드 단위 트랜잭션으로 중단되어도 일부만 반영된다 (`key_version`으로 재개 가능)
- 실행 전 DB 백업 확인을 스크립트가 안내한다

---

## 4. 메모리 취급 (NFR-209)

```python
# 올바른 사용 — 실제 API 호출 인자에서만 평문화
pwd = connection.password.get_secret_value()
SmartConnect(host=..., user=..., pwd=pwd, ...)

# 금지
logger.debug("connecting with %s", connection.password.get_secret_value())
cache.set(key, connection.password.get_secret_value())
json.dumps({"password": connection.password.get_secret_value()})
```

- 복호화 값은 **어댑터 세션 생성 시점에만** 만든다
- 파일·Redis·임시 파일에 쓰지 않는다
- 어댑터에는 평문 `str`이 아니라 `SecretStr`을 전달한다

---

## 5. 노출 방지 (NFR-203)

| 지점 | 조치 | 검증 |
|---|---|---|
| 도메인 모델 | `Connection.password: SecretStr`, `__repr__` 재정의 (계획 02 §10) | `repr()` 테스트 |
| 로깅 | 마스킹 필터 (§5.1) | 패턴별 단위 테스트 |
| API 응답 | 응답 스키마에 비밀번호 필드 부재. `has_password: bool`만 | 스키마 검사 |
| 예외 메시지 | 어댑터가 `sanitize_message` 통과 후 도메인 예외 생성 | 계약 테스트 |
| 감사 로그 | 값이 아니라 "변경됨" 사실만 | §6.2 |
| DB 덤프 | 암호문만 저장 | 마이그레이션 검토 |

### 5.1 로그 마스킹 필터 (`masking.py`)

**서드파티 라이브러리를 신뢰하지 않는다.** pyVmomi·pypsrp가 예외 메시지나 디버그 로그에 접속 정보를 넣을 수 있다.

```python
MASK = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # password: xxx / password=xxx / "password": "xxx"
    (re.compile(r"""(['"]?(?:password|pwd|passwd|secret|token|api[_-]?key)['"]?\s*[:=]\s*['"]?)([^'"\s,;}&]+)""",
                re.IGNORECASE), r"\1" + MASK),
    # URL 내 자격증명 — scheme://user:pass@host
    (re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)"), r"\1" + MASK + r"\3"),
    # Authorization 헤더
    (re.compile(r"(Authorization:\s*\w+\s+)(\S+)", re.IGNORECASE), r"\1" + MASK),
    # Basic 인증 base64
    (re.compile(r"(Basic\s+)([A-Za-z0-9+/=]{8,})"), r"\1" + MASK),
)


def mask_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def sanitize_message(text: str, *, secrets: Iterable[str] = ()) -> str:
    """패턴 마스킹 + 알려진 비밀값 직접 제거."""
    out = mask_text(text)
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, MASK)
    return out


class CredentialMaskingFilter(logging.Filter):
    """모든 로그 레코드에서 자격증명 패턴을 마스킹한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_text(record.msg)
        if record.args:
            record.args = tuple(
                mask_text(a) if isinstance(a, str) else a for a in _as_tuple(record.args)
            )
        for key, val in list(vars(record).items()):
            if isinstance(val, str) and key not in _RESERVED:
                setattr(record, key, mask_text(val))
        return True
```

**루트 로거가 아니라 모든 핸들러에 부착한다.** 필터는 로거 단위로만 상속되지 않으므로,
핸들러에 붙여야 하위 로거의 레코드까지 통과한다.

```python
def install_masking(handlers: Iterable[logging.Handler]) -> None:
    f = CredentialMaskingFilter()
    for h in handlers:
        h.addFilter(f)
```

### 5.2 예외 정제

어댑터가 도메인 예외를 만들 때 원본 메시지를 정제한다 (계획 04 §8, 계획 05 §10).

```python
raise UnreachableError(sanitize_message(str(exc), secrets=[conn.password.get_secret_value()]))
```

`raise ... from None`으로 체이닝을 끊어 원본 예외가 트레이스백에 남지 않게 한다.

---

## 6. 감사 로그 (FR-1004, NFR-205·206)

### 6.1 도메인 모델 (`src/domain/audit.py`)

```python
class AuditAction(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    CONNECTION_CREATE = "connection.create"
    CONNECTION_UPDATE = "connection.update"
    CONNECTION_DELETE = "connection.delete"
    CREDENTIAL_UPDATE = "credential.update"
    CONNECTION_TEST = "connection.test"
    COLLECTION_TRIGGER = "collection.trigger"
    METADATA_UPDATE = "metadata.update"
    METADATA_BULK_UPDATE = "metadata.bulk_update"
    EXPORT = "export"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    SCOPE_UPDATE = "scope.update"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    occurred_at: datetime
    actor: str
    actor_ip: str | None
    action: AuditAction
    target_type: str | None
    target_id: str | None
    result: Literal["success", "failure"]
    detail: Mapping[str, Any] = field(default_factory=dict)
```

### 6.2 detail 구성 — 화이트리스트 방식

**요청 본문을 통째로 넣으면 자격증명이 섞인다.** 액션별로 담을 키를 명시한다.

```python
ALLOWED_DETAIL_KEYS: dict[AuditAction, frozenset[str]] = {
    AuditAction.CONNECTION_CREATE: frozenset({"display_name", "kind", "address", "port", "auth_method"}),
    AuditAction.CONNECTION_UPDATE: frozenset({"changed_fields"}),       # 필드명만, 값 제외
    AuditAction.CREDENTIAL_UPDATE: frozenset({"changed"}),              # {"changed": true}만
    AuditAction.CONNECTION_DELETE: frozenset({"display_name", "impact"}),
    AuditAction.CONNECTION_TEST: frozenset({"stages", "is_usable"}),
    AuditAction.METADATA_UPDATE: frozenset({"changed_fields", "before", "after"}),
    AuditAction.EXPORT: frozenset({"criteria", "row_count", "format", "truncated"}),
    AuditAction.LOGIN: frozenset({"username", "reason"}),
}


def build_detail(action: AuditAction, raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = ALLOWED_DETAIL_KEYS.get(action, frozenset())
    return {k: _scrub(v) for k, v in raw.items() if k in allowed}


def _scrub(value: Any) -> Any:
    return mask_text(value) if isinstance(value, str) else value
```

**`CREDENTIAL_UPDATE`는 값을 절대 기록하지 않는다.** 이전 비밀번호도, 새 비밀번호도, 길이도 남기지 않는다.

### 6.3 기록 서비스

```python
class AuditService:
    async def record(self, event: AuditEvent) -> None:
        """감사 이벤트를 기록한다. 실패해도 본 작업을 막지 않는다."""
        try:
            await self._repo.insert(event)
        except Exception:
            logger.exception("감사 로그 기록 실패",
                             extra={"action": event.action.value, "actor": event.actor})
            # 예외를 전파하지 않는다 — 감사 실패로 조회·수정이 막히면 안 된다
```

> **트레이드오프**: 감사 실패 시 작업을 막을지 여부는 규제 환경에 따라 다르다.
> 이 프로젝트는 조회 중심이므로 **가용성을 우선**한다. 규제 요구가 생기면 `strict_audit` 설정으로 전환한다.

### 6.4 스키마

```sql
CREATE TABLE audit_events (
    event_id     UUID PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor        TEXT NOT NULL,
    actor_ip     INET,
    action       TEXT NOT NULL,
    target_type  TEXT,
    target_id    TEXT,
    result       TEXT NOT NULL,
    detail       JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_audit_time ON audit_events (occurred_at DESC);
CREATE INDEX idx_audit_actor ON audit_events (actor, occurred_at DESC);
CREATE INDEX idx_audit_action ON audit_events (action, occurred_at DESC);
CREATE INDEX idx_audit_target ON audit_events (target_type, target_id, occurred_at DESC);
```

**감사 로그는 애플리케이션 로그와 다른 저장소·보존 정책을 갖는다.** 별도 설정 키로 분리한다.

### 6.5 내보내기 감사 (NFR-206)

인벤토리 정보(IP·호스트명·OS)는 그 자체로 공격 표면 정보다. 대량 내보내기는 유출 경로이므로 추적한다.

```python
await audit.record(AuditEvent(
    ..., action=AuditAction.EXPORT,
    detail=build_detail(AuditAction.EXPORT, {
        "criteria": criteria_summary,      # 필터 조건 요약
        "row_count": rows,
        "format": "xlsx",
        "truncated": was_truncated,        # 상한 절단 여부 (계획 13 §3.1)
    }),
))
```

---

## 7. 인증 실패 판정 (FR-114 연계)

이 계획은 **판정 재료**를 제공하고, 재시도 정책은 계획 06의 워커가 적용한다.

- 어댑터가 인증 실패를 `AuthenticationError`(`retryable=False`)로 변환 (계획 04 §8, 05 §10)
- `retry_async`가 `retryable`을 보고 즉시 중단 (계획 02 §4.1)
- 워커가 연결 상태를 `CREDENTIAL_ERROR`로 전환하고 **스케줄을 해제** (계획 06 §8.2)

**절대 하면 안 되는 것**: 인증 실패를 일반 오류로 취급해 3회 재시도. AD 계정 잠금으로 이어진다 (CST-05).

---

## 8. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `KeyProvider` + `EnvKeyProvider` | 32바이트 검증, 구버전 키 로드 |
| 2 | `CredentialCipher` | 암복호 왕복, `key_version` 파싱, 잘못된 키 시 예외, 변조 감지(InvalidTag) |
| 3 | `masking.py` | 패턴 5종, 실제 pyVmomi/pypsrp 예외 문자열 샘플 |
| 4 | `CredentialMaskingFilter` | 핸들러 부착 후 로그 출력에 비밀번호 부재 |
| 5 | `AuditEvent` + 스키마 | JSONB 직렬화 |
| 6 | `build_detail` 화이트리스트 | 허용 외 키 제거, 문자열 스크럽 |
| 7 | `AuditService.record` | 기록 실패 시 예외 미전파 |
| 8 | 키 교체 스크립트 | dry-run, 샘플 재암호화 후 복호화 성공 |

## 9. 완료 기준

- [ ] 자격증명이 DB에 평문으로 저장되지 않음
- [ ] 저장 형식에 `key_version`이 포함되어 키 교체 경로가 확보됨
- [ ] 암호문 변조 시 복호화가 실패 (AES-GCM 인증 태그)
- [ ] `Connection`을 `repr()`·f-string·로그에 넣어도 비밀번호 미노출
- [ ] 마스킹 필터가 URL·헤더·JSON·Basic 인증 형태를 모두 가림
- [ ] API 응답 스키마에 비밀번호 필드가 존재하지 않음
- [ ] 감사 `detail`에 자격증명이 들어갈 경로가 없음 (화이트리스트)
- [ ] `CREDENTIAL_UPDATE` 이벤트에 값·길이가 기록되지 않음
- [ ] 감사 기록 실패가 본 작업을 막지 않음
- [ ] 내보내기가 조건·건수·절단 여부와 함께 기록됨
- [ ] 키 교체 스크립트가 dry-run 기본이고 재암호화 후 복호화 성공
- [ ] `/security-review` 실행 결과 자격증명 노출 지적 0건

## 10. 주의사항

- **`SecretStr`을 쓴다고 안전해지지 않는다.** `get_secret_value()` 결과를 로그·예외·JSON에 넣으면 그대로 노출된다. 호출 지점을 최소화하고 코드 리뷰에서 전수 확인한다.
- 복호화 실패 사유를 상세히 노출하지 않는다 (오라클 공격 방지).
- 테스트 픽스처에 실제 자격증명을 쓰지 않는다. 커밋되면 회수 불가다.
- 마스킹 필터는 **로거가 아니라 핸들러**에 부착한다.
- 감사 로그를 애플리케이션 로그와 같은 저장소에 두지 않는다. 보존 기간과 접근 권한이 다르다.
- `.env`의 `dict` 타입 설정은 JSON 형식으로 써야 한다 (Known Mistakes 1번).
