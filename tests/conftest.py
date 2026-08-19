"""테스트 공통 설정 — 환경변수 기본값만 둔다.

**DB 픽스처는 여기 두지 않는다** (`tests/integration/conftest.py`로 분리).
`autouse` DB 픽스처를 루트에 두면 순수 단위 테스트까지 PostgreSQL을 요구하게 되어,
DB 없이는 매퍼·마스킹 테스트조차 돌릴 수 없다. 빠른 피드백 루프가 사라진다.

여기 남는 것은 `src.config.Settings`가 필수 필드(DB URL·JWT 시크릿·암호화 키)를
요구하기 때문에 **import 시점에 필요한 값**뿐이다. 실제 연결은 하지 않는다.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "PORTAL_DATABASE_URL", "postgresql+asyncpg://portal:devpass@127.0.0.1:55432/wzoneportal"
)
os.environ.setdefault("PORTAL_JWT_SECRET", "test-secret-value-for-pytest-only")
# base64('pytest-only-key-32bytes-padding!') — 실제 자격증명이 아니다 (계획 10 §10)
os.environ.setdefault(
    "PORTAL_CREDENTIAL_ENCRYPTION_KEY", "cHl0ZXN0LW9ubHkta2V5LTMyYnl0ZXMtcGFkZGluZyE="
)
os.environ.setdefault("PORTAL_COOKIE_SECURE", "false")
# 부트스트랩 관리자는 테스트에서 직접 만든다
os.environ.pop("PORTAL_BOOTSTRAP_ADMIN_USERNAME", None)
os.environ.pop("PORTAL_BOOTSTRAP_ADMIN_PASSWORD", None)
