# 01. 프로젝트 구조 및 설정

> Wave: 0 (모든 구현의 선행)
> 계층: 전체
> 담당 요건: — (기반 작업)
> 의존: 없음

## 1. 목적

디렉토리 스켈레톤, 패키지 설정, 환경변수 체계, 로깅을 구축한다.
이 계획이 완료되면 다른 Wave가 병렬로 시작할 수 있다.

## 2. 디렉토리 구조

```
wzonecloudportal/
├── src/
│   ├── __init__.py
│   ├── config.py                    # [config] pydantic-settings 설정
│   ├── main.py                      # [entry] FastAPI 앱 + 워커 진입점
│   ├── domain/                      # [domain] 의존 없음
│   │   ├── __init__.py
│   │   ├── resource.py              #   VM/Host/Cluster/Datastore/Network/Snapshot
│   │   ├── connection.py            #   연결 정보, 연결 유형, 인증 방식
│   │   ├── metadata.py              #   소유자·환경 등 포탈 부여 메타데이터
│   │   ├── history.py               #   변경 이력, 수집 이력
│   │   ├── auth.py                  #   사용자, 역할, 조회 범위
│   │   ├── audit.py                 #   감사 이벤트
│   │   ├── ports.py                 #   HypervisorInventoryReader Protocol
│   │   └── exceptions.py            #   도메인 예외 계층
│   ├── utils/                       # [utils] 의존 없음
│   │   ├── __init__.py
│   │   ├── net.py                   #   IP·MAC 정규화, FQDN 검증
│   │   └── retry.py                 #   재시도 데코레이터 (인증 실패 제외)
│   ├── infrastructure/              # [infrastructure]
│   │   ├── __init__.py
│   │   ├── vcenter/                 #   pyVmomi 수집 어댑터 (계획 04)
│   │   ├── hyperv/                  #   WinRM/WMI 수집 어댑터 (계획 05)
│   │   ├── db/                      #   SQLAlchemy 세션·모델 (계획 06)
│   │   ├── repository/              #   저장소 구현 (계획 06)
│   │   ├── cache/                   #   Redis 클라이언트
│   │   └── security/                #   자격증명 암호화, 토큰 (계획 10)
│   ├── application/                 # [application] 유스케이스
│   │   ├── __init__.py
│   │   ├── inventory_query.py       #   조회·검색 (계획 07)
│   │   ├── metadata_service.py      #   메타데이터 관리 (계획 07)
│   │   ├── connection_service.py    #   연결 관리 유스케이스 (계획 08)
│   │   ├── change_history.py        #   변경 이력 (계획 12)
│   │   └── report.py                #   리포트 (계획 13)
│   ├── orchestration/               # [orchestration] 워커
│   │   ├── __init__.py
│   │   ├── scheduler.py             #   수집 스케줄러 (계획 06)
│   │   └── collector.py             #   수집 실행기 (계획 06)
│   └── api/                         # [interface] FastAPI
│       ├── __init__.py
│       ├── deps.py                  #   DI, 인증 의존성
│       ├── schemas/                 #   요청·응답 Pydantic 모델
│       └── routes/                  #   엔드포인트 (계획 08)
├── static/                          # 포탈 UI (계획 11)
├── tests/
│   ├── conftest.py
│   ├── fakes/                       # 목 커넥터 (CST-04)
│   ├── unit/
│   ├── contract/                    # 어댑터 계약 테스트
│   └── integration/
├── migrations/                      # Alembic
├── scripts/arch_check.py            # (기존)
├── agents/                          # (기존)
├── docs/  plans/  spec.md  CLAUDE.md
├── pyproject.toml
└── .env.example
```

**`MODULE_LAYER_MAP` 등록 확인**: 위 구조는 `scripts/arch_check.py`의 현재 매핑과 일치한다.
새 최상위 패키지를 추가하면 반드시 등록한다.

## 3. pyproject.toml

```toml
[project]
name = "wzonecloudportal"
requires-python = ">=3.11"
dependencies = [
    "fastapi", "uvicorn[standard]",
    "pydantic>=2", "pydantic-settings",
    "sqlalchemy[asyncio]>=2", "asyncpg", "alembic",
    "redis",
    "pyvmomi",                # vCenter 수집
    "pypsrp",                 # Hyper-V WinRM
    "cryptography",           # 자격증명 암호화
    "python-jose[cryptography]", "passlib[bcrypt]",   # 인증
    "openpyxl",               # Excel 내보내기
    "apscheduler",            # 수집 스케줄
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "pytest-cov", "ruff", "mypy"]

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.mypy]
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

> **검증 필요**: `pypsrp`의 CredSSP 지원 범위와 비동기 지원 여부는 계획 05에서 확인한다 (`docs/00_research_notes.md` §11-5).

## 4. 설정 (`src/config.py`)

pydantic-settings 기반. **`.env`의 `list`/`dict` 타입 필드는 JSON 배열 형식으로 작성해야 한다** (CLAUDE.md Known Mistakes 1번).

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PORTAL_")

    # 서버
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    log_level: str = "INFO"

    # DB / 캐시
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # 보안
    jwt_secret: SecretStr
    jwt_expire_minutes: int = 480
    credential_encryption_key: SecretStr      # 계획 10, NFR-208

    # 수집 (미확정 요건의 임시 기본값 — plans/README.md §5)
    default_collection_interval_minutes: int = 360
    collection_timeout_seconds: int = 60
    collection_max_retries: int = 3
    collection_max_concurrent_connections: int = 4
    stale_resource_grace_days: int = 7
    history_retention_days: int = 365
    data_freshness_warning_hours: int = 12
```

**금지**: `model_post_init`에서 `os.getenv()`를 쓰지 않는다. `.env` 값이 `os.environ`에 주입되지 않아 systemd 환경에서 누락된다 (Known Mistakes 3번). 필요하면 `AliasChoices`를 사용한다.

## 5. 환경변수 (`.env.example`)

```bash
PORTAL_DATABASE_URL=postgresql+asyncpg://portal:password@localhost:5432/wzoneportal
PORTAL_REDIS_URL=redis://localhost:6379/0
PORTAL_JWT_SECRET=change-me
PORTAL_CREDENTIAL_ENCRYPTION_KEY=change-me-base64-32bytes
PORTAL_LOG_LEVEL=INFO
```

`.env`는 `.gitignore`에 포함되어 있다. `.env.example`만 커밋한다.

## 6. 로깅

- 구조화 로깅(JSON) 사용. 필드: `timestamp`, `level`, `logger`, `message`, `connection_id`, `run_id`, `user_id`
- **자격증명·토큰은 어떤 레벨에서도 기록하지 않는다** (NFR-203). 계획 10의 마스킹 필터를 로깅 파이프라인에 적용
- 수집 워커 로그에는 `run_id`를 필수 포함하여 수집 이력(FR-205)과 대조 가능하게 한다

## 7. 구현 순서

1. 디렉토리·`__init__.py` 생성 → 검증: `python scripts/arch_check.py` 통과 (위반 0)
2. `pyproject.toml` 작성 → 검증: `pip install -e ".[dev]"` 성공
3. `src/config.py` + `.env.example` → 검증: `Settings()` 인스턴스화 성공
4. 로깅 설정 → 검증: JSON 로그 출력 확인
5. `tests/conftest.py` 뼈대 → 검증: `pytest` 수집 0건 실패 없이 종료

## 8. 완료 기준

- [ ] `python scripts/arch_check.py --ci` 통과
- [ ] `pytest` 실행 시 에러 없이 종료
- [ ] `.env.example`만으로 `Settings` 로드 성공
- [ ] `uvicorn src.main:app` 기동 후 `/api/v1/health`가 200 반환 (빈 앱이라도)

## 9. 주의사항

- 이 단계에서 비즈니스 로직을 작성하지 않는다. 빈 패키지와 설정만 만든다.
- `src/domain/`에는 외부 패키지 import를 최소화한다. pydantic·dataclasses 정도만 허용한다.
