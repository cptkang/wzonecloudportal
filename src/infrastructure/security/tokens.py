"""JWT 발급·검증 (계획 09 §4.2).

**조회 범위를 토큰에 넣지 않는다.** 범위 변경이 토큰 만료까지 지연되면 권한 회수가
늦어진다. 범위는 요청마다 DB에서 조회한다 (계획 09 §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from jose import JWTError, jwt

from src.config import Settings
from src.domain.auth import AuthenticatedUser, Role
from src.domain.exceptions import AuthenticationError

ALGORITHM = "HS256"
COOKIE_NAME = "portal_session"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: UUID
    username: str
    role: Role
    exp: int
    iat: int
    #: 토큰 고유 ID — 서버측 무효화(블랙리스트) 도입 대비 (Step 8)
    jti: str


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
        return TokenClaims(
            sub=UUID(raw["sub"]),
            username=raw["username"],
            role=Role(raw["role"]),
            exp=int(raw["exp"]),
            iat=int(raw["iat"]),
            jti=raw["jti"],
        )
    except (JWTError, KeyError, ValueError):
        raise AuthenticationError("토큰이 유효하지 않습니다.") from None
