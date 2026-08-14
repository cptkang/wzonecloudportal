"""암호화 키 제공자 (계획 10 §3.3).

키 관리 방식이 미확정(NFR-208)이므로 **인터페이스로 분리**하여 나중에 구현체만
교체한다.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from typing import Protocol

from src.config import Settings

KEY_SIZE = 32  # AES-256


class KeyProvider(Protocol):
    def get_key(self, version: int) -> bytes: ...

    @property
    def current_version(self) -> int: ...

    def known_versions(self) -> Sequence[int]: ...


class EnvKeyProvider:
    """환경변수 기반 (임시 기본 구현).

    ``PORTAL_CREDENTIAL_ENCRYPTION_KEY``  현재 키 (base64, 32바이트)
    ``PORTAL_CREDENTIAL_KEY_VERSION``     현재 버전 (기본 1)
    ``PORTAL_CREDENTIAL_LEGACY_KEYS``     구버전 키 (JSON, 교체 중에만 필요)
    """

    def __init__(self, settings: Settings) -> None:
        self._keys: dict[int, bytes] = {}
        current = _decode_key(settings.credential_encryption_key.get_secret_value())
        self._current = settings.credential_key_version
        self._keys[self._current] = current
        for version, raw in settings.credential_legacy_keys.items():
            self._keys[int(version)] = _decode_key(raw)

    @property
    def current_version(self) -> int:
        return self._current

    def get_key(self, version: int) -> bytes:
        if version not in self._keys:
            raise KeyError(f"키 버전 {version}을 찾을 수 없습니다.")
        return self._keys[version]

    def known_versions(self) -> Sequence[int]:
        return sorted(self._keys)


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("암호화 키는 base64 문자열이어야 합니다.") from exc
    if len(key) != KEY_SIZE:
        raise ValueError(f"암호화 키는 {KEY_SIZE}바이트(base64)여야 합니다.")
    return key
