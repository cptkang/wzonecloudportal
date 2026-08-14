"""자격증명 암호화·마스킹·비밀번호 (계획 10 §3·§5, 계획 09 §4.1)."""

from __future__ import annotations

import base64

import pytest
from pydantic import SecretStr

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import ConnectionKind
from src.domain.exceptions import PortalError, ValidationError
from src.infrastructure.security.cipher import CredentialCipher
from src.infrastructure.security.keys import EnvKeyProvider
from src.infrastructure.security.masking import mask_text, sanitize_message
from src.infrastructure.security.password import (
    MAX_PASSWORD_BYTES,
    hash_password,
    verify_password,
)

SECRET = "SuperSecret!234"


def _settings(**overrides: object) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "jwt_secret": SecretStr("x" * 32),
        "credential_encryption_key": SecretStr(base64.b64encode(b"k" * 32).decode()),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _cipher(**overrides: object) -> CredentialCipher:
    return CredentialCipher(EnvKeyProvider(_settings(**overrides)))


def test_encrypt_decrypt_roundtrip() -> None:
    cipher = _cipher()
    stored = cipher.encrypt(SecretStr(SECRET))
    assert SECRET not in stored
    assert cipher.decrypt(stored).get_secret_value() == SECRET


def test_stored_format_carries_key_version() -> None:
    """버전 접두사가 없으면 키 교체가 불가능해진다."""
    stored = _cipher(credential_key_version=3).encrypt(SecretStr(SECRET))
    assert stored.split("$")[0] == "3"


def test_tampered_ciphertext_is_rejected() -> None:
    """AES-GCM 인증 태그가 변조를 감지한다."""
    cipher = _cipher()
    stored = cipher.encrypt(SecretStr(SECRET))
    version, nonce, ct = stored.split("$", 2)
    tampered = f"{version}${nonce}${ct[:-2]}AA"

    with pytest.raises(PortalError) as ei:
        cipher.decrypt(tampered)
    # 실패 사유를 상세히 노출하지 않는다 (오라클 공격 방지)
    assert "복호화할 수 없습니다" in ei.value.message


def test_needs_rotation_detects_old_version() -> None:
    old = _cipher(credential_key_version=1).encrypt(SecretStr(SECRET))
    newer = _cipher(
        credential_key_version=2,
        credential_legacy_keys={"1": base64.b64encode(b"k" * 32).decode()},
    )
    assert newer.needs_rotation(old) is True
    assert newer.needs_rotation(newer.encrypt(SecretStr(SECRET))) is False


def test_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError, match="32바이트"):
        EnvKeyProvider(_settings(credential_encryption_key=SecretStr(base64.b64encode(b"short").decode())))


def test_connection_repr_hides_password() -> None:
    from uuid import uuid4

    conn = Connection(
        connection_id=uuid4(),
        kind=ConnectionKind.VCENTER,
        display_name="vCenter DC1",
        address="vcsa.example.invalid",
        port=443,
        username="svc-inventory@vsphere.local",
        password=SecretStr(SECRET),
    )
    for rendered in (repr(conn), f"{conn}", str(conn)):
        assert SECRET not in rendered


@pytest.mark.parametrize(
    "raw",
    [
        'password: SuperSecret!234',
        '{"password": "SuperSecret!234"}',
        "pwd=SuperSecret!234",
        "https://user:SuperSecret!234@vcsa.example.invalid/sdk",
        "Authorization: Bearer SuperSecret!234",
    ],
)
def test_masking_covers_common_shapes(raw: str) -> None:
    assert SECRET not in mask_text(raw)


def test_sanitize_message_removes_known_secret() -> None:
    """패턴에 걸리지 않는 형태도 알려진 값이면 지운다."""
    raw = f"connect failed for {SECRET} at host"
    assert SECRET not in sanitize_message(raw, secrets=[SECRET])


def test_password_hash_verify_roundtrip() -> None:
    hashed = hash_password(SecretStr("correct-horse-battery"))
    assert hashed != "correct-horse-battery"
    ok, rehash = verify_password(SecretStr("correct-horse-battery"), hashed)
    assert ok is True
    assert rehash is None
    assert verify_password(SecretStr("wrong-password-value"), hashed)[0] is False


def test_password_length_policy() -> None:
    with pytest.raises(ValidationError):
        hash_password(SecretStr("short"))
    with pytest.raises(ValidationError):
        hash_password(SecretStr("a" * (MAX_PASSWORD_BYTES + 1)))
