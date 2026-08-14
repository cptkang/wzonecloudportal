"""사용자 비밀번호 해시 (계획 09 §4.1).

계획 09는 `passlib`의 `CryptContext`를 지정했으나, 이 환경의 `bcrypt` 5.x와
호환되지 않아 **`bcrypt`를 직접 사용한다** (D-015). 공개 함수 시그니처는 계획과 같다.

**bcrypt는 72바이트 초과 입력을 처리하지 못한다.** 조용히 잘라내면 서로 다른
비밀번호가 같은 해시를 갖게 되므로, 입력 단계에서 길이를 제한하고 여기서도 검증한다.
"""

from __future__ import annotations

import secrets
import string

import bcrypt
from pydantic import SecretStr

from src.domain.exceptions import ValidationError

#: bcrypt 절단 한계 (계획 09 §4.5의 비밀번호 규칙 10~72자와 같은 근거)
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 10
BCRYPT_ROUNDS = 12

#: 존재하지 않는 계정에도 같은 시간을 쓰기 위한 더미 해시 (계획 09 §4.3)
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def validate_password_policy(plain: SecretStr) -> None:
    """길이 정책을 검사한다. bcrypt 한계를 넘으면 해시 자체가 실패한다."""
    raw = plain.get_secret_value()
    if len(raw) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.", field="password"
        )
    if len(raw.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValidationError(
            f"비밀번호는 {MAX_PASSWORD_BYTES}바이트를 넘을 수 없습니다.", field="password"
        )


def hash_password(plain: SecretStr) -> str:
    validate_password_policy(plain)
    raw = plain.get_secret_value().encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: SecretStr, hashed: str) -> tuple[bool, str | None]:
    """검증 결과와, 재해시가 필요하면 새 해시를 반환한다.

    비용 파라미터를 올린 뒤 기존 사용자의 해시를 점진적으로 옮기기 위한 경로다.
    """
    raw = plain.get_secret_value().encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False, None
    try:
        ok = bcrypt.checkpw(raw, hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False, None
    if ok and _needs_rehash(hashed):
        return True, bcrypt.hashpw(raw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")
    return ok, None


def fake_verify() -> None:
    """존재하지 않는 계정에도 해시 검증 시간을 소비해 타이밍 차이를 줄인다.

    이것이 없으면 응답 시간만으로 계정 존재를 알아낼 수 있다 (계획 09 §4.3).
    """
    bcrypt.checkpw(b"dummy-password-for-timing", _DUMMY_HASH)


_TEMP_ALPHABET = string.ascii_letters + string.digits


def generate_temporary_password(length: int = 16) -> SecretStr:
    """임시 비밀번호를 생성한다 (FR-1008).

    반환값은 응답에 1회만 실리고 저장·로깅하지 않는다.
    """
    body = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length - 2))
    return SecretStr(f"{body}a1")


def _needs_rehash(hashed: str) -> bool:
    """저장된 해시의 비용 파라미터가 현재 설정보다 낮은지 판정한다."""
    try:
        # 형식: $2b$12$<salt+hash>
        rounds = int(hashed.split("$")[2])
    except (IndexError, ValueError):
        return False
    return rounds < BCRYPT_ROUNDS
