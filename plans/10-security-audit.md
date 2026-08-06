# 10. 자격증명 보호 및 감사 로그

> Wave: 1
> 계층: infrastructure (`src/infrastructure/security/`), domain (`src/domain/audit.py`)
> 담당 요건: NFR-203·208·209, FR-104(자격증명 관리), FR-108, FR-1004(감사 로그), NFR-205·206
> 의존: 01, 02
> 관련 결정: D-008

## 1. 목적

하이퍼바이저 접속 자격증명을 안전하게 저장·사용하고, 관리 작업의 감사 추적을 남긴다.

**이 계획의 결함은 곧 조직 전체의 침해 지점이 된다.** 통합 포탈은 다수 vCenter/Hyper-V의 자격증명을 한곳에 모으기 때문이다.

## 2. 자격증명 저장 (NFR-203, NFR-208)

### 2.1 암호화 방식

- 대칭키 암호화(AES-256-GCM). `cryptography` 라이브러리의 `AESGCM` 사용
- 레코드마다 고유 nonce 생성. nonce는 암호문과 함께 저장
- 저장 형식: `{key_version}:{base64(nonce)}:{base64(ciphertext)}`
- **`key_version` 접두사를 반드시 포함한다.** 키 교체 시 기존 레코드를 식별해 재암호화해야 하는데, 버전이 없으면 어느 키로 복호화할지 알 수 없다

```python
class CredentialCipher:
    """자격증명 암호화. 키는 설정에서 주입받으며 코드·DB에 평문으로 두지 않는다."""

    def encrypt(self, plaintext: SecretStr) -> str: ...
    def decrypt(self, stored: str) -> SecretStr: ...
    def needs_rotation(self, stored: str) -> bool: ...   # 구버전 키로 암호화된 레코드 판별
```

### 2.2 키 관리 — `[TODO]` NFR-208

확정 전 임시 구현: 환경변수 `PORTAL_CREDENTIAL_ENCRYPTION_KEY`(base64 32바이트).

**키 교체 절차를 처음부터 만들어 둔다** (나중에 넣기 어렵다):

```
scripts/rotate_credential_key.py --old-key-version 1 --new-key-version 2
  → 모든 연결 레코드를 구키로 복호화 → 신키로 재암호화 → key_version 갱신
  → 실행 전 DB 백업 확인, 트랜잭션 단위 처리, 진행률 로그
```

키 관리 방식(환경변수 / OS 키스토어 / 외부 KMS)이 확정되면 `CredentialCipher`의 키 조회 부분만 교체할 수 있도록 **키 제공자를 인터페이스로 분리**한다.

```python
class KeyProvider(Protocol):
    def get_key(self, version: int) -> bytes: ...
    @property
    def current_version(self) -> int: ...
```

### 2.3 메모리 취급 (NFR-209)

- 복호화된 값은 `SecretStr`로 감싸 로그·`repr`에 노출되지 않게 한다
- **복호화 값을 파일·Redis 캐시·임시 파일에 쓰지 않는다.** 어댑터 세션 생성 시점에만 복호화한다
- 어댑터에 평문 문자열이 아니라 `SecretStr`을 전달하고, 실제 API 호출 직전에 `get_secret_value()`를 호출한다

### 2.4 노출 방지 장치

| 지점 | 조치 |
|---|---|
| 도메인 모델 | `Connection.password`를 `SecretStr`로 선언 |
| 로깅 | 로깅 필터에서 자격증명 패턴 마스킹 (아래 §2.5) |
| API 응답 | 응답 스키마에 비밀번호 필드를 아예 두지 않음. 존재 여부만 `has_password: bool` |
| 예외 메시지 | 어댑터가 도메인 예외로 변환할 때 원본 메시지에서 자격증명 제거 |
| 감사 로그 | 값이 아니라 "변경됨" 사실만 기록 (FR-1004) |

### 2.5 로깅 마스킹 필터

pyVmomi·pypsrp가 예외 메시지나 디버그 로그에 접속 정보를 포함할 수 있다.
**서드파티 라이브러리를 신뢰하지 말고 출력 단에서 한 번 더 거른다.**

```python
class CredentialMaskingFilter(logging.Filter):
    """로그 레코드에서 자격증명으로 보이는 문자열을 마스킹한다."""
    PATTERNS = [
        re.compile(r"(password['\"]?\s*[:=]\s*['\"]?)([^'\"\s,}]+)", re.I),
        re.compile(r"(://[^:/@]+:)([^@]+)(@)"),          # URL 내 자격증명
        re.compile(r"(Authorization:\s*\w+\s+)(\S+)", re.I),
    ]
```

루트 로거에 부착하여 모든 핸들러에 적용한다.

## 3. 감사 로그 (FR-1004, NFR-205·206)

### 3.1 기록 대상

| 이벤트 | 기록 항목 |
|---|---|
| 로그인 성공·실패 | 사용자, IP, 시각, 결과 |
| 연결 등록 | 사용자, 연결 ID, 표시명, 유형, 주소 |
| 연결 수정 | 사용자, 연결 ID, **변경된 필드 목록**(값 아님) |
| 자격증명 변경 | 사용자, 연결 ID, "변경됨" 사실만 |
| 연결 삭제 | 사용자, 연결 ID, 영향 자원 수 |
| 연결 테스트 실행 | 사용자, 연결 ID, 단계별 결과 |
| 메타데이터 변경 | 사용자, 자원 ID, 필드, 이전값 → 새값 |
| 리포트·목록 내보내기 | 사용자, 조회 조건, 결과 건수 (NFR-206) |

> **내보내기가 감사 대상인 이유**: 인벤토리 정보(IP·호스트명·OS)는 그 자체로 공격 표면 정보다.
> 대량 내보내기는 정보 유출 경로이므로 추적되어야 한다.

### 3.2 도메인 모델 (`src/domain/audit.py`)

```python
class AuditAction(StrEnum):
    LOGIN = "login"
    CONNECTION_CREATE = "connection.create"
    CONNECTION_UPDATE = "connection.update"
    CONNECTION_DELETE = "connection.delete"
    CREDENTIAL_UPDATE = "credential.update"
    CONNECTION_TEST = "connection.test"
    METADATA_UPDATE = "metadata.update"
    EXPORT = "export"

@dataclass(frozen=True)
class AuditEvent:
    event_id: UUID
    occurred_at: datetime
    actor: str                       # 사용자 식별자
    actor_ip: str | None
    action: AuditAction
    target_type: str | None          # "connection" | "resource" | ...
    target_id: str | None
    result: Literal["success", "failure"]
    detail: dict[str, Any]           # 변경 필드 목록, 건수 등 — 자격증명 값 금지
```

### 3.3 기록 규약

- **감사 로그는 실패해도 본 작업을 막지 않는다.** 단, 기록 실패 자체를 에러 로그로 남긴다
- `detail`에 들어갈 값은 화이트리스트 방식으로 구성한다. 요청 본문을 통째로 넣으면 자격증명이 섞인다
- 보존 기간은 변경 이력과 동일 정책을 따른다 (`[TODO]` FR-706)

## 4. 인증 실패와 재시도 (FR-114 연계)

이 계획은 **자격증명 오류를 어떻게 판정하는지**를 제공한다. 재시도 정책 자체는 계획 06의 워커가 적용한다.

- 어댑터가 인증 실패를 `AuthenticationError`(계획 02, `retryable = False`)로 변환
- `src/utils/retry.py`의 재시도 데코레이터는 `exc.retryable`이 False면 즉시 중단
- 연결 상태를 "자격증명 오류"로 전환하고 스케줄에서 제외 (계획 06)

**절대 하면 안 되는 것**: 인증 실패를 일반 오류로 취급해 3회 재시도. AD 계정 잠금으로 이어진다 (CST-05).

## 5. 구현 순서

1. `KeyProvider` + `CredentialCipher` → 검증: 암복호 왕복, key_version 파싱, 잘못된 키 시 예외
2. `CredentialMaskingFilter` → 검증: 패턴별 마스킹 단위 테스트, 실제 pyVmomi 예외 문자열 샘플로 확인
3. `AuditEvent` 도메인 모델 + 저장소 인터페이스 → 검증: 직렬화
4. 감사 기록 서비스 → 검증: 기록 실패 시 본 작업 계속 진행
5. 키 교체 스크립트 → 검증: 샘플 데이터로 재암호화 후 복호화 성공

## 6. 완료 기준

- [ ] 자격증명이 DB에 평문으로 저장되지 않음
- [ ] `key_version` 접두사로 키 교체 경로가 확보됨
- [ ] `Connection` 객체를 `print()`/`repr()`/로그에 넣어도 비밀번호가 보이지 않음
- [ ] 마스킹 필터가 URL·헤더·JSON 형태의 자격증명을 모두 가림
- [ ] `/security-review` 실행 결과 자격증명 노출 지적 0건
- [ ] 감사 이벤트에 자격증명 값이 들어갈 수 있는 경로가 없음

## 7. 주의사항

- **`SecretStr`을 쓴다고 안전해지지 않는다.** `get_secret_value()` 결과를 로그·예외에 넣으면 그대로 노출된다. 호출 지점을 최소화한다.
- 테스트 픽스처에 실제 자격증명을 쓰지 않는다. 커밋되면 회수 불가다.
- `.gitignore`에 인증서·키 패턴(`*.pem`, `*.key`, `*.pfx`)이 이미 등록되어 있다. 새 형식을 추가하면 함께 등록한다.
- 감사 로그를 애플리케이션 로그와 같은 저장소에 두지 않는다. 보존 기간과 접근 권한이 다르다.
