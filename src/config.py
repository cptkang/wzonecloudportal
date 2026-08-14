"""애플리케이션 설정 (계획 01 §4의 Step 1 축소판).

Step 1에 필요한 항목만 정의한다. Redis·스케줄·리포트 설정은 해당 Step에서 추가한다.

**금지**: `model_post_init`에서 `os.getenv()`를 쓰지 않는다. pydantic-settings의 `.env`
로딩은 `os.environ`에 주입하지 않아 systemd 환경에서 값이 누락된다
(CLAUDE.md Known Mistakes 3번).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PORTAL_",
        extra="ignore",
    )

    # ── 서버 ───────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    log_level: str = "INFO"

    # ── DB ─────────────────────────────────
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ── 인증 (계획 09) ─────────────────────
    jwt_secret: SecretStr
    jwt_expire_minutes: int = 480
    cookie_secure: bool = False
    max_failed_login_attempts: int = 5
    lockout_minutes: int = 15
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    # ── 자격증명 암호화 (계획 10, NFR-208) ──
    credential_encryption_key: SecretStr
    credential_key_version: int = 1
    #: 키 교체 중에만 사용한다. dict이므로 `.env`에 JSON으로 쓴다 (Known Mistakes 1번).
    credential_legacy_keys: dict[str, str] = {}

    # ── 수집 (계획 06) ─────────────────────
    collection_timeout_seconds: int = 60
    collection_max_retries: int = 3
    collection_batch_size: int = 500
    #: vCenter RetrievePropertiesEx의 maxObjects (계획 04 §4.2)
    collection_page_size: int = 500

    # ── 신선도 (FR-502) ────────────────────
    data_freshness_warning_hours: int = 24


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 1회만 로드한다."""
    return Settings()  # type: ignore[call-arg]  # 값은 .env/환경변수에서 온다
