"""자격증명 암복호 (계획 10 §3). 축소하지 않는다.

저장 형식::

    {key_version}${base64url(nonce)}${base64url(ciphertext_with_tag)}

**`key_version` 접두사가 필수다.** 키 교체 시 어느 키로 복호화할지 알아야 하며,
버전 없이 저장하면 교체가 불가능해진다.

AES-GCM을 쓰는 이유는 인증 태그가 포함되어 변조를 감지하기 때문이다.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from src.domain.exceptions import PortalError
from src.infrastructure.security.keys import KeyProvider

CIPHER_SEPARATOR = "$"
NONCE_SIZE = 12  # AES-GCM 권장


class CredentialCipher:
    """자격증명 암복호. 키는 KeyProvider가 공급하며 코드·DB에 평문으로 두지 않는다."""

    def __init__(self, keys: KeyProvider) -> None:
        self._keys = keys

    def encrypt(self, plaintext: SecretStr) -> str:
        version = self._keys.current_version
        key = self._keys.get_key(version)
        nonce = os.urandom(NONCE_SIZE)
        ct = AESGCM(key).encrypt(nonce, plaintext.get_secret_value().encode("utf-8"), None)
        return CIPHER_SEPARATOR.join([str(version), _b64(nonce), _b64(ct)])

    def decrypt(self, stored: str) -> SecretStr:
        try:
            version_s, nonce_b64, ct_b64 = stored.split(CIPHER_SEPARATOR, 2)
            key = self._keys.get_key(int(version_s))
            plain = AESGCM(key).decrypt(_unb64(nonce_b64), _unb64(ct_b64), None)
        except (ValueError, KeyError, InvalidTag):
            # 실패 사유를 상세히 노출하지 않는다 (오라클 공격 방지).
            # `from None`으로 체이닝을 끊어 원본이 트레이스백에 남지 않게 한다.
            raise PortalError("자격증명을 복호화할 수 없습니다.") from None
        return SecretStr(plain.decode("utf-8"))

    def needs_rotation(self, stored: str) -> bool:
        try:
            return int(stored.split(CIPHER_SEPARATOR, 1)[0]) != self._keys.current_version
        except (ValueError, IndexError):
            return True


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))
